# Copyright © 2019-present gsfernandes81

# This file is part of "dd" henceforth referred to as "destiny-director".

# destiny-director is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later version.

# "destiny-director" is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License along with
# destiny-director. If not, see <https://www.gnu.org/licenses/>.

import asyncio as aio
import logging
from enum import Enum
from random import randint
from time import perf_counter
from types import TracebackType
from typing import Any, Callable, Coroutine, Dict, Optional, Tuple, Type

import dateparser
import hikari as h
import lightbulb as lb
import miru as m
import regex as re
from lightbulb.ext import tasks

from ...beacon.bot import CachedFetchBot
from ...common import cfg
from ...common.schemas import MirroredChannel, MirroredMessage, ServerStatistics
from .. import bot, utils

re_markdown_link = re.compile(r"\[(.*?)\]\(.*?\)")


class TimedSemaphore(aio.Semaphore):
    """Semaphore to ensure no more than value requests per period are made

    This is to stay well within discord api rate limits while avoiding errors"""

    def __init__(self, value: int = 30, period=1):
        super().__init__(value)
        self.period = period

    async def release(self) -> None:
        """Delay release until period has passed"""
        await aio.sleep(self.period)
        return super().release()

    async def __aexit__(
        self,
        exc_type: Type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Coroutine[Any, Any, None]:
        return await self.release()


discord_api_semaphore = TimedSemaphore(value=45)


class MirrorOperationType(Enum):
    """Enum to represent the type of mirror operation"""

    SEND = 1
    UPDATE = 2
    DELETE = 3


class KernelWorkControlRegistry:
    """Registry to keep track of current KernelWorkControl instances"""

    def __init__(self):
        # Note: the dict is structured as follows:
        # {
        #    (
        #       source_channel_id,
        #       source_message_id,
        #   ): KernelWorkControl
        # }
        self._registry: Dict[Tuple[int, int], KernelWorkControl] = {}

    def register(self, control: "KernelWorkControl"):
        """Register a KernelWorkControl instance"""
        key = (
            control.source_channel_id,
            control.source_message_id,
        )
        if key in self._registry:
            # Consider making this an async wait for the other task to finish
            # Remember to alter the code for whether we will block for all operation
            # types individually or cumulatively if you do make this change
            raise ValueError(f"KernelWorkControl already registered for {key}")
        self._registry[key] = control

    def cancel(
        self,
        source_channel_id: int,
        source_message_id: int,
    ):
        """Cancel a KernelWorkControl instance"""
        key = (source_channel_id, source_message_id)
        if key in self._registry:
            control = self._registry.pop(key)
            if control.mirror_operation_type != MirrorOperationType.UPDATE:
                raise ValueError(
                    "Can only cancel mirror updates. This message has an operation "
                    f"of type '{control.mirror_operation_type}' running"
                )
            control.cancel()
        else:
            raise ValueError("This message does not have any operations in progress")


kernel_work_control_registry = KernelWorkControlRegistry()


class KernelWorkTracker:
    """Class to track the progress of all kernels for a particular mirror event

    source: Dict[int, int] = {source_channel_id: source_message_id} with size 1
    targets: Dict[int, Optional[int]] = {target_channel_id: target_message_id}
    """

    def __init__(
        self,
        source: Dict[int, int],
        targets: Dict[int, Optional[int]],
        mirror_operation_type: MirrorOperationType,
        retry_threshold: int = 3,
    ):
        self.retry_threshold = retry_threshold
        self.source_channel_id = source.keys().__iter__().__next__()
        self.source_message_id = source[self.source_channel_id]
        self.mirror_operation_type = mirror_operation_type
        self._targets = targets
        self._tries: Dict[int, Optional[int]] = {
            target_id: 0 for target_id in self._targets.keys()
        }
        self._completed_successfully: Dict[int, int] = {}
        self._scheduled: Dict[int, Optional[int]] = {}
        self.cancelled: Dict[int, Optional[int]] = {}

    def _report_try(self, channel_id: int):
        self._tries[channel_id] += 1
        self._scheduled.pop(channel_id)

    def report_scheduled(self, channel_id: int, message_id: Optional[int] = None):
        if channel_id in self._scheduled:
            e = ValueError(
                f"Target already scheduled. source_message_id: {self.source_message_id} id:"
                f"{channel_id}.{self._scheduled[channel_id]} vs incoming: "
                f"{channel_id}.{message_id}"
            )
            logging.exception(e)
            raise e
        else:
            self._scheduled[channel_id] = message_id

    def report_completed(self, channel_id: int, message_id: int):
        self._report_try(channel_id)
        self._completed_successfully[channel_id] = message_id

    def report_failure(self, channel_id: int):
        self._report_try(channel_id)

    @property
    def failed_targets(self) -> Dict[int, Optional[int]]:
        "IDs that have failed more than the retry threshold and will not be retried"
        return {
            channel_id: message_id
            for channel_id, message_id in self._targets.items()
            if self._tries[channel_id] >= self.retry_threshold
        }

    @property
    def successful_targets(self) -> Dict[int, int]:
        return self._completed_successfully

    @property
    def _targets_tried_at_least_once(self) -> Dict[int, Optional[int]]:
        return {
            target: self._targets[target]
            for target, tries_ in self._tries.items()
            if tries_ > 0
        }

    @property
    def targets_not_yet_tried(self) -> Dict[int, Optional[int]]:
        return {
            target: self._targets[target]
            for target, tries_ in self._tries.items()
            if tries_ == 0
        }

    @property
    def is_every_target_tried(self) -> bool:
        return len(self._targets_tried_at_least_once) == len(self._targets)

    @property
    def targets_to_schedule(self) -> Dict[int, Optional[int]]:
        return {
            channel_id: message_id
            for channel_id, message_id in self._targets.items()
            if channel_id not in self._scheduled
            and channel_id not in self.failed_targets
            and channel_id not in self.successful_targets
            and channel_id not in self.cancelled
        }

    @property
    def targets_being_retried(self) -> Dict[int, Optional[int]]:
        return {
            channel_id: message_id
            for channel_id, message_id in self._targets.items()
            if channel_id in self._targets_tried_at_least_once
            and channel_id not in self.failed_targets
            and channel_id not in self.successful_targets
        }

    @property
    def is_work_left_to_do(self) -> bool:
        return self.targets_to_schedule or self._scheduled


class KernelWorkControl(KernelWorkTracker):
    def __init__(
        self,
        source: Dict[int, int],
        targets: Dict[int, Optional[int]],
        role_ping_per_ch_id: Dict[int, Optional[int]],
        mirror_operation_type: MirrorOperationType,
        kernel: Callable,
        retry_threshold: int = 3,
    ):
        super().__init__(
            source,
            targets,
            mirror_operation_type=mirror_operation_type,
            retry_threshold=retry_threshold,
        )
        self.role_ping_per_ch_id = role_ping_per_ch_id
        kernel_work_control_registry.register(self)
        self._kernel = kernel
        self._tasks = set()

    @staticmethod
    async def _kernel(self, ch_id: int, msg_id: int, delay: int = 0):
        raise NotImplementedError

    async def run_till_completion(self):
        is_first_loop = True
        while self.is_work_left_to_do:
            tasks = [
                aio.create_task(
                    self._kernel(
                        self,
                        dest_ch_id,
                        dest_msg_id,
                        delay=0 if is_first_loop else randint(180, 300),
                    )
                )
                for dest_ch_id, dest_msg_id in self.targets_to_schedule.items()
            ]
            self._tasks.update(tasks)
            await aio.wait(self._tasks, return_when=aio.ALL_COMPLETED)
            is_first_loop = False

    def cancel(self, *arg, **kwargs):
        for task in self._tasks:
            task: aio.Task
            task.cancel()

        self.cancelled.update(self._scheduled)
        self.cancelled.update(self.targets_to_schedule)
        self._scheduled.clear()


def _get_message_summary(msg: h.Message, default: str = "Link") -> str:
    if msg.content:
        summary = msg.content.split("\n")[0]

    for embed in msg.embeds:
        if embed.title:
            summary = embed.title
        if embed.description:
            summary = msg.embeds[0].description.split("\n")[0]

    if not summary:
        return default

    summary = summary.replace("*", "")
    summary = summary.replace("_", "")
    summary = summary.replace("#", "")
    summary = summary.strip("{}")
    summary = summary.strip("<>")
    summary = summary.strip("")
    summary = summary.capitalize()

    # Use re_markdown_link to remove links replacing
    # them with just the text unless the text is empty
    summary = re_markdown_link.sub(r"\1", summary) or summary

    return summary


async def _continue_logging_mirror_progress_till_completion(
    log_message: h.Message, tracker: KernelWorkTracker, start_time: float
):
    COMPLETED = 2
    RETRYING = 3
    FAILED = 4
    REMAINING = 5
    TIME_TAKEN = 6
    TIME_TAKE_TO_TRY_ALL_ONCE = 7

    while True:
        tries = 0
        final_update = not tracker.is_work_left_to_do
        while (tries < 1) or final_update:
            try:
                time_taken = round(perf_counter() - start_time, 2)
                time_taken = (
                    f"{time_taken} seconds"
                    if time_taken < 60
                    else f"{time_taken // 60} minutes {round(time_taken % 60, 2)} seconds"
                )

                embed = log_message.embeds[0]
                embed.edit_field(
                    COMPLETED, h.UNDEFINED, str(len(tracker.successful_targets))
                )
                embed.edit_field(
                    RETRYING, h.UNDEFINED, str(len(tracker.targets_being_retried))
                )
                embed.edit_field(FAILED, h.UNDEFINED, str(len(tracker.failed_targets)))
                embed.edit_field(
                    REMAINING,
                    h.UNDEFINED,
                    str(len(tracker.targets_not_yet_tried)),
                )
                embed.edit_field(TIME_TAKEN, h.UNDEFINED, str(time_taken))
                if (
                    tracker.is_every_target_tried
                    and embed.fields[TIME_TAKE_TO_TRY_ALL_ONCE].value == "TBC"
                ):
                    embed.edit_field(
                        TIME_TAKE_TO_TRY_ALL_ONCE, h.UNDEFINED, str(time_taken)
                    )

                if tracker.failed_targets:
                    embed.color = cfg.embed_error_color

                if final_update:
                    if tracker.cancelled:
                        embed.set_footer(
                            text="❌ Cancelled"
                            + (" with errors" if tracker.failed_targets else ""),
                        )
                    else:
                        embed.set_footer(
                            text="✅ Completed"
                            + (" with errors" if tracker.failed_targets else ""),
                        )

                await log_message.edit(embeds=[embed], components=[])

                if final_update:
                    return

            except Exception as e:
                e.add_note("Failed to log mirror progress due to exception\n")
                logging.exception(e)
                tries += 1
                await aio.sleep(5**tries)
            else:
                await aio.sleep(5)  # Wait 5 seconds between updates
                break


class LogCancelButton(m.Button):
    def __init__(self, callback=None):
        super().__init__(style=h.ButtonStyle.DANGER, label="Cancel Mirror")
        self._callback = callback

    async def callback(self, ctx: m.ViewContext):
        await self._callback(self, ctx)


async def log_mirror_progress_to_discord(
    bot: bot.CachedFetchBot,
    control: KernelWorkControl,
    source_message: h.Message | None,
    start_time: float,
    title: Optional[str] = "Mirror progress",
    source_channel: Optional[h.GuildChannel] = None,
    enable_cancellation: Optional[bool] = False,
):
    tries = 0
    while True:
        try:
            log_channel: h.TextableGuildChannel = await bot.fetch_channel(
                cfg.log_channel
            )

            time_taken = round(perf_counter() - start_time, 2)
            time_taken = (
                f"{time_taken} seconds"
                if time_taken < 60
                else f"{time_taken // 60} minutes {round(time_taken % 60, 2)} seconds"
            )

            if source_channel or source_message:
                source_channel: h.TextableGuildChannel = (
                    await bot.fetch_channel(source_message.channel_id)
                    if not source_channel
                    else (
                        source_channel
                        if isinstance(source_channel, h.GuildChannel)
                        else await bot.fetch_channel(source_channel)
                    )
                )

            if source_channel:
                source_guild = await bot.fetch_guild(source_channel.guild_id)
                source_message_link = source_message.make_link(source_guild)
            else:
                source_message_link = ""

            if source_message:
                source_message_summary = _get_message_summary(source_message)
            else:
                source_message_summary = "Unknown"

            if source_channel:
                source_channel_link = (
                    "https://discord.com/channels/"
                    + str(source_channel.guild_id)
                    + "/"
                    + str(source_channel.id)
                )

            embed = h.Embed(color=cfg.embed_default_color, title=title)
            embed.add_field(
                "Source message",
                f"[{source_message_summary}]({source_message_link})"
                if source_message_link
                else source_message_summary,
                inline=True,
            ).add_field(
                "Source channel",
                f"[{source_channel.name}]({source_channel_link})"
                if source_channel
                else "Unknown",
                inline=True,
            ).add_field(
                "Completed", str(len(control.successful_targets)), inline=True
            ).add_field(
                "Retrying", str(len(control.targets_being_retried)), inline=True
            ).add_field(
                "Failed", str(len(control.failed_targets)), inline=True
            ).add_field(
                "Remaining",
                str(len(control.targets_not_yet_tried)),
                inline=True,
            ).add_field("Time taken", f"{time_taken}").add_field(
                "Time to try all channels once",
                time_taken if control.is_every_target_tried else "TBC",
            )

            if source_message:
                if source_message.embeds and source_message.embeds[0].image:
                    embed.set_thumbnail(source_message.embeds[0].image.url)
                elif source_message.attachments and source_message.attachments[
                    0
                ].media_type.startswith("image"):
                    embed.set_thumbnail(source_message.attachments[0].url)

            if not control.is_work_left_to_do:
                embed.set_footer(
                    text="✅ Completed",
                )
            else:
                embed.set_footer(
                    text="⏳ In progress",
                )

            if enable_cancellation:

                async def cancel(self: m.Button, ctx: m.ViewContext):
                    # Cancel the kernel and remove the message
                    bot: lb.BotApp = ctx.bot
                    if ctx.author.id not in await bot.fetch_owner_ids():
                        # Ignore non owners
                        pass

                    await control.cancel()
                    self.disabled = True
                    await ctx.edit_response(components=ctx.view)
                    self.view.stop()

                cancel_button = LogCancelButton(callback=cancel)
                view = m.View(timeout=60 * 60 * 7)
                view.add_item(cancel_button)
                log_message = await log_channel.send(embed, components=view)
                await view.start(message=log_message)
                # Do not await view.wait() since it will block the event loop
                # and we want to continue logging progress
            else:
                log_message = await log_channel.send(embed)
            break
        except Exception as e:
            e.add_note("Failed to log mirror progress due to exception\n")
            logging.exception(e)
            tries += 1
            await aio.sleep(5**tries)

    aio.create_task(
        _continue_logging_mirror_progress_till_completion(
            log_message, control, start_time
        )
    )


def ignore_non_src_channels(func):
    async def wrapped_func(event: h.MessageEvent):
        if isinstance(event, h.MessageCreateEvent) or isinstance(
            event, h.MessageUpdateEvent
        ):
            msg = event.message
        elif isinstance(event, h.MessageDeleteEvent):
            msg = event.old_message

        if (
            msg
            and int(msg.channel_id) in await MirroredChannel.get_or_fetch_all_srcs()
            # also keep going if we are running in a test env
            # keep this towards the end so short circuiting in test_env
            # does not hide logic errors
            or cfg.test_env
        ):
            return await func(event)

    return wrapped_func


async def handle_waiting_for_crosspost(
    msg: h.Message, bot: lb.BotApp, channel: h.TextableChannel, wait_for_crosspost: bool
):
    backoff_timer = 30
    while True:
        try:
            channel_name_or_id = str(utils.followable_name(id=channel.id))
            logging.info(
                f"MessageCreateEvent received for message in channel: {channel_name_or_id}"
            )

            # The below is to make sure we aren't using a reference to a message that
            # has already changed (in particular, has already been crossposted)
            # Using such a reference would result in us waiting forever for a crosspost
            # event that has already fired
            msg = await bot.rest.fetch_message(msg.channel_id, msg.id)

            if wait_for_crosspost and h.MessageFlag.CROSSPOSTED not in msg.flags:
                logging.info(
                    f"Message in channel {channel_name_or_id} not crossposted, waiting..."
                )
                await bot.wait_for(
                    h.MessageUpdateEvent,
                    timeout=12 * 60 * 60,
                    predicate=lambda e: e.message.id == msg.id
                    and e.message.flags
                    and h.MessageFlag.CROSSPOSTED in e.message.flags,
                )
                logging.info(
                    f"Crosspost event received for message in channel {channel_name_or_id}, "
                    + "continuing..."
                )
        except aio.TimeoutError:
            return
        except Exception as e:
            await utils.discord_error_logger(bot, e)
            await aio.sleep(backoff_timer)
            backoff_timer += 30 / backoff_timer
        else:
            break


def add_role_ping_to_msg(
    msg_content: str,
    dest_channel_id: int,
    role_ping_per_ch_id: Dict[int, Optional[int]],
) -> str:
    """Add role ping to message content if specified for this channel

    In case the roll ping is 0, no changes are made."""
    role_ping = int(role_ping_per_ch_id.get(dest_channel_id) or 0)
    if role_ping:
        if msg_content:
            msg_content = msg_content.strip("\n") + "\n\n"
        else:
            msg_content = ""
        msg_content += f"||<@&{role_ping}>||"
    return msg_content


@ignore_non_src_channels
@utils.ignore_self
async def message_create_repeater(event: h.MessageCreateEvent):
    await message_create_repeater_impl(
        event.message,
        event.app,
        await event.app.fetch_channel(event.message.channel_id),
    )


async def message_create_repeater_impl(
    msg: h.Message,
    bot: bot.CachedFetchBot,
    channel: h.TextableChannel,
    wait_for_crosspost: bool = True,
):
    # Wait for message to be crossposted before mirroring if requested
    await handle_waiting_for_crosspost(
        msg=msg,
        bot=bot,
        channel=channel,
        wait_for_crosspost=wait_for_crosspost,
    )

    # Fetch the message again to avoid stale date in case there was an
    # edit very close to the crosspost event
    msg = await bot.rest.fetch_message(msg.channel_id, msg.id)

    mirrors = await MirroredChannel.fetch_dests(channel.id)
    mirror_mention_ids = await MirroredChannel.fetch_mirror_and_role_mention_id(
        channel.id
    )
    # Always guard against infinite loops through posting to the source channel
    mirrors = list(filter(lambda x: x != channel.id, mirrors))

    mirror_start_time = perf_counter()

    # Remove discord auto image embeds
    msg.embeds = utils.filter_discord_autoembeds(msg)

    async def kernel(
        control: KernelWorkControl,
        ch_id: int,
        msg_id: int = None,
        delay: int = 0,
    ):
        # NOTE: msg is passed as a closure to the kernel function here
        control.report_scheduled(ch_id)
        await aio.sleep(delay)

        try:
            channel: h.TextableChannel = await bot.fetch_channel(ch_id)

            if not isinstance(channel, h.TextableChannel):
                # Ignore non textable channels
                raise ValueError("Channel is not textable")

            # msg_content = msg.content
            msg_content = add_role_ping_to_msg(
                msg.content, ch_id, control.role_ping_per_ch_id
            )

            async with discord_api_semaphore:
                # Send the message
                # Note: components are no longer mirrored. This allows use to
                # use buttons for admin purposes in the main server
                mirrored_msg = await channel.send(
                    msg_content,
                    attachments=msg.attachments,
                    embeds=msg.embeds,
                    role_mentions=True,
                )
        except Exception as e:
            e.add_note(
                f"Asking control for a retry for message-send to channel {ch_id} "
                + "due to exception\n"
            )
            logging.exception(e)
            control.report_failure(ch_id)
            return
        else:
            control.report_completed(ch_id, mirrored_msg.id)

        if isinstance(channel, h.GuildNewsChannel):
            # If the channel is a news channel then crosspost the message as well
            crosspost_backoff = 30
            for _ in range(3):
                try:
                    async with discord_api_semaphore:
                        await bot.rest.crosspost_message(ch_id, mirrored_msg.id)

                except Exception as e:
                    if (
                        isinstance(e, h.BadRequestError)
                        and "This message has already been crossposted" in e.message
                    ):
                        # If the message has already been crossposted
                        # then we can ignore the error
                        break

                    e.add_note(
                        f"Failed to crosspost message in channel {ch_id} "
                        + "due to exception\n"
                    )
                    logging.exception(e)
                    await aio.sleep(crosspost_backoff)
                    crosspost_backoff = crosspost_backoff * 2
                else:
                    break

    control = KernelWorkControl(
        source={channel.id: msg.id},
        targets={ch_id: None for ch_id in mirrors},
        role_ping_per_ch_id=mirror_mention_ids,
        mirror_operation_type=MirrorOperationType.SEND,
        kernel=kernel,
    )

    await log_mirror_progress_to_discord(
        bot=bot,
        control=control,
        source_message=msg,
        start_time=mirror_start_time,
        title="Mirror send progress",
    )

    await control.run_till_completion()

    logging.info("Completed all mirrors in " + str(perf_counter() - mirror_start_time))

    # Log successes, failures and message pairs to the db
    maybe_exceptions = await aio.gather(
        MirroredChannel.log_legacy_mirror_failure_in_batch(
            channel.id, list(control.failed_targets.keys())
        ),
        MirroredChannel.log_legacy_mirror_success_in_batch(
            channel.id, list(control.successful_targets.keys())
        ),
        MirroredMessage.add_msgs_in_batch(
            dest_msgs=list(control.successful_targets.values()),
            dest_channels=list(control.successful_targets.keys()),
            source_msg=msg.id,
            source_channel=channel.id,
        ),
        return_exceptions=True,
    )

    # Log exceptions working with the db to the console
    if any(maybe_exceptions):
        logging.error(
            "Error logging mirror success/failure in db: "
            + ", ".join([str(exception) for exception in maybe_exceptions if exception])
        )

    # Auto disable persistently failing mirrors
    if cfg.disable_bad_channels:
        disabled_mirrors = await MirroredChannel.disable_legacy_failing_mirrors()

    if disabled_mirrors:
        logging.warning(
            ("Disabled " if cfg.disable_bad_channels else "Would disable ")
            + str(len(disabled_mirrors))
            + " mirrors: "
            + ", ".join(
                [f"{mirror.src_id}: {mirror.dest_id}" for mirror in disabled_mirrors]
            )
        )


@ignore_non_src_channels
@utils.ignore_self
async def message_update_repeater(event: h.MessageUpdateEvent):
    await message_update_repeater_impl(event.message, event.app)


async def message_update_repeater_impl(msg: h.Message, bot: bot.CachedFetchBot):
    backoff_timer = 30
    while True:
        try:
            msgs_to_update = await MirroredMessage.get_dest_msgs_and_channels(msg.id)
            if not msgs_to_update:
                # Return if this message was not mirrored for any reason
                return

        except Exception as e:
            await utils.discord_error_logger(bot, e)
            await aio.sleep(backoff_timer)
            backoff_timer += 30 / backoff_timer
        else:
            break

    mirror_start_time = perf_counter()

    # Fetch message again since update events aren't guaranteed to
    # include unchanged data
    msg = await bot.rest.fetch_message(msg.channel_id, msg.id)

    # Remove discord auto image embeds
    msg.embeds = utils.filter_discord_autoembeds(msg)

    async def kernel(
        control: KernelWorkControl,
        ch_id: int,
        msg_id: int,
        delay: int = 0,
    ):
        control.report_scheduled(ch_id, msg_id)
        await aio.sleep(delay)

        try:
            async with discord_api_semaphore:
                dest_msg = await bot.fetch_message(ch_id, msg_id)
            async with discord_api_semaphore:
                msg_content = add_role_ping_to_msg(
                    msg.content, ch_id, control.role_ping_per_ch_id
                )

                # Note: components are no longer mirrored. This allows use to
                # use buttons for admin purposes in the main server
                await dest_msg.edit(
                    msg_content,
                    attachments=msg.attachments,
                    embeds=msg.embeds,
                    role_mentions=True,
                )
        except Exception as e:
            e.add_note(
                f"Asking control for a retry for message-update to channel {ch_id} "
                + "due to exception\n"
            )
            logging.exception(e)
            control.report_failure(ch_id)
        else:
            control.report_completed(ch_id, msg_id)

    control = KernelWorkControl(
        source={msg.channel_id: msg.id},
        targets={channel_id: dest_msg_id for dest_msg_id, channel_id in msgs_to_update},
        mirror_operation_type=MirrorOperationType.UPDATE,
        retry_threshold=2,
        kernel=kernel,
    )

    await log_mirror_progress_to_discord(
        bot=bot,
        control=control,
        source_message=msg,
        start_time=mirror_start_time,
        title="Mirror update progress",
        enable_cancellation=True,
    )

    await control.run_till_completion()


@ignore_non_src_channels
async def message_delete_repeater(event: h.MessageDeleteEvent):
    msg_id = event.message_id
    msg = event.old_message
    bot = event.app

    await message_delete_repeater_impl(msg_id, msg, bot)


async def message_delete_repeater_impl(
    msg_id: int, msg: Optional[h.Message], bot: bot.CachedFetchBot
):
    backoff_timer = 30
    while True:
        try:
            msgs_to_delete = await MirroredMessage.get_dest_msgs_and_channels(msg_id)
            if not msgs_to_delete:
                # Return if this message was not mirrored for any reason
                return

        except Exception as e:
            await utils.discord_error_logger(bot, e)
            await aio.sleep(backoff_timer)
            backoff_timer += 30 / backoff_timer
        else:
            break

    mirror_start_time = perf_counter()

    async def kernel(
        tracker,
        ch_id: int,
        msg_id: int,
        delay: int = 0,
    ):
        tracker.report_scheduled(ch_id, msg_id)
        await aio.sleep(delay)

        try:
            async with discord_api_semaphore:
                dest_msg: h.Message = await bot.fetch_message(ch_id, msg_id)
            async with discord_api_semaphore:
                await dest_msg.delete()

        except Exception as e:
            e.add_note(
                f"Asking control for a retry for message-delete to channel {ch_id} "
                + "due to exception\n"
            )
            logging.exception(e)
            tracker.report_failure(dest_msg.channel_id)
        else:
            tracker.report_completed(dest_msg.channel_id, msg_id)

    control = KernelWorkControl(
        source={None: msg_id},
        targets={channel_id: dest_msg_id for dest_msg_id, channel_id in msgs_to_delete},
        mirror_operation_type=MirrorOperationType.DELETE,
        retry_threshold=2,
        kernel=kernel,
    )

    await log_mirror_progress_to_discord(
        bot=bot,
        control=control,
        source_message=msg,
        start_time=mirror_start_time,
        title="Mirror delete progress",
    )

    await control.run_till_completion()


@tasks.task(d=7, auto_start=True, wait_before_execution=False, pass_app=True)
async def refresh_server_sizes(bot: bot.CachedFetchBot):
    await utils.wait_till_lightbulb_started(bot)
    await aio.sleep(randint(30, 60))

    backoff_timer = 30
    while True:
        try:
            server_populations = {}
            async for guild in bot.rest.fetch_my_guilds():
                if not isinstance(guild, h.RESTGuild):
                    guild = await bot.rest.fetch_guild(guild.id)

                try:
                    server_populations[guild.id] = guild.approximate_member_count
                except Exception as e:
                    logging.exception(e)

            existing_servers = await ServerStatistics.fetch_server_ids()
            existing_servers = list(
                set(existing_servers).intersection(set(server_populations.keys()))
            )
            new_servers = list(set(server_populations.keys()) - set(existing_servers))

            new_servers_bins = [
                new_servers[i : i + 50] for i in range(0, len(new_servers), 50)
            ]
            for new_servers_bin in new_servers_bins:
                await ServerStatistics.add_servers_in_batch(
                    new_servers_bin,
                    [server_populations[server_id] for server_id in new_servers_bin],
                )

            existing_servers_bins = [
                existing_servers[i : i + 50]
                for i in range(0, len(existing_servers), 50)
            ]
            for existing_servers_bin in existing_servers_bins:
                await ServerStatistics.update_population_in_batch(
                    existing_servers_bin,
                    [
                        server_populations[server_id]
                        for server_id in existing_servers_bin
                    ],
                )

        except Exception as e:
            should_retry_ = backoff_timer <= 24 * 60 * 60

            exception_note = "Error refreshing server sizes, "
            exception_note += (
                f"backing off for {backoff_timer} minutes"
                if should_retry_
                else "giving up"
            )
            e.add_note(exception_note)

            await utils.discord_error_logger(bot, e)

            if not should_retry_:
                break

            await aio.sleep(backoff_timer * 60)
            backoff_timer = backoff_timer * 4

        else:
            break


@tasks.task(d=1, auto_start=True, wait_before_execution=False, pass_app=True)
async def prune_message_db(bot: bot.CachedFetchBot):
    await aio.sleep(randint(120, 1800))
    try:
        await MirroredMessage.prune()
    except Exception as e:
        e.add_note("Exception during routine pruning of MirroredMessage")
        await utils.discord_error_logger(bot, e)


# Command group for all mirror commands
mirror_group = lb.command(
    "mirror",
    description="Command group for all mirror control/administration commands",
    guilds=[cfg.control_discord_server_id],
    hidden=True,
)(
    lb.implements(
        lb.SlashCommandGroup,
    )(lambda: None)
)


@mirror_group.child
@lb.option("from_date", description="Date to start from", type=str)
@lb.command(
    "undo_auto_disable",
    description="Undo auto disable of a channel due to repeated post failures",
    guilds=[cfg.control_discord_server_id],
    pass_options=True,
    auto_defer=True,
)
@lb.implements(lb.SlashSubCommand)
async def undo_auto_disable(ctx: lb.Context, from_date: str):
    if ctx.author.id not in await ctx.bot.fetch_owner_ids():
        return

    from_date = dateparser.parse(from_date)

    mirrors = await MirroredChannel.undo_auto_disable_for_failure(since=from_date)
    response = f"Undid auto disable since {from_date} for channels {mirrors}"
    logging.info(response)
    await ctx.respond(response)


@mirror_group.child
@lb.option("dest_server_id", description="Destination server id")
@lb.option("dest", description="Destination channel")
@lb.option("src", description="Source channel")
@lb.command(
    "manual_add",
    description="Manually add a mirror to the database",
    guilds=[cfg.control_discord_server_id],
    pass_options=True,
    auto_defer=True,
)
@lb.implements(lb.SlashSubCommand)
async def manual_add(ctx: lb.Context, src: str, dest: str, dest_server_id: str):
    if ctx.author.id not in await ctx.bot.fetch_owner_ids():
        return

    src = int(src)
    dest = int(dest)
    dest_server_id = int(dest_server_id)

    await MirroredChannel.add_mirror(
        src, dest, dest_server_id=dest_server_id, legacy=True
    )
    await ctx.respond("Added mirror")


@lb.command(
    "mirror_send",
    description="Manually mirror a message",
    guilds=[cfg.control_discord_server_id, cfg.kyber_discord_server_id],
    hidden=True,
    ephemeral=True,
)
@lb.implements(lb.MessageCommand)
async def manual_mirror_send(ctx: lb.MessageContext):
    if ctx.author.id not in await ctx.bot.fetch_owner_ids():
        await ctx.respond("You are not allowed to use this command...")
        return

    await ctx.respond("Mirroring message...")
    logging.info(f"Manually mirroring for channel id {ctx.options.target.channel_id}")
    await message_create_repeater_impl(
        ctx.options.target,
        ctx.app,
        await ctx.app.fetch_channel(ctx.channel_id),
        wait_for_crosspost=False,
    )
    await ctx.edit_last_response("Mirrored message.")


@lb.command(
    "mirror_update",
    description="Manually update a mirrored message",
    guilds=[cfg.control_discord_server_id, cfg.kyber_discord_server_id],
    hidden=True,
    ephemeral=True,
)
@lb.implements(lb.MessageCommand)
async def manual_mirror_update(ctx: lb.MessageContext):
    if ctx.author.id not in await ctx.bot.fetch_owner_ids():
        await ctx.respond("You are not allowed to use this command...")
        return

    await ctx.respond("Updating message...")
    logging.info(
        f"Manually updating mirrored message {ctx.options.target.id} "
        f" in channel id {ctx.options.target.channel_id}"
    )
    await message_update_repeater_impl(ctx.options.target, ctx.app)
    await ctx.edit_last_response("Updated message.")


@mirror_group.child
@lb.option("message_id", description="Message to delete", type=str)
@lb.command(
    "delete_msg",
    description="Manually delete a mirrored message",
    guilds=[cfg.control_discord_server_id, cfg.kyber_discord_server_id],
    hidden=True,
    ephemeral=True,
    pass_options=True,
)
@lb.implements(lb.SlashSubCommand)
async def manual_mirror_delete(ctx: lb.SlashContext, message_id: str):
    if ctx.author.id not in await ctx.bot.fetch_owner_ids():
        await ctx.respond("You are not allowed to use this command...")
        return

    mid = int(message_id)
    bot: lb.BotApp = ctx.app

    await ctx.respond("Deleting message...")
    logging.info(f"Manually deleting mirrored message {mid}")
    await message_delete_repeater_impl(mid, bot.cache.get_message(mid), ctx.app)
    await ctx.edit_last_response("Deleted messages.")


@lb.command(
    "mirror_cancel",
    description="Manually cancels a message mirror currently in progress",
    guilds=[cfg.control_discord_server_id, cfg.kyber_discord_server_id],
    hidden=True,
    ephemeral=True,
)
@lb.implements(lb.MessageCommand)
async def mirror_cancel(ctx: lb.MessageContext):
    if ctx.author.id not in await ctx.bot.fetch_owner_ids():
        await ctx.respond("You are not allowed to use this command...")
        return

    message: h.Message = ctx.options.target
    try:
        kernel_work_control_registry.cancel(message.channel_id, message.id)
    except ValueError as e:
        await ctx.respond("Failed to cancel mirror: " + str(e))
        return
    else:
        await ctx.respond("Cancelled mirror")


@mirror_group.child
@lb.option(
    "channel_id", description="Destination channel id to show details of", type=str
)
@lb.command(
    "source_details",
    description="Show details about a channels mirror sources if any",
    guilds=[cfg.control_discord_server_id],
    hidden=True,
    pass_options=True,
)
@lb.implements(lb.SlashSubCommand)
async def mirror_source_details(ctx: lb.SlashContext, channel_id: str):
    if ctx.author.id not in await ctx.bot.fetch_owner_ids():
        await ctx.respond("You are not allowed to use this command...")
        return

    # channel_id = 722159088321953913
    channel_id = int(channel_id)
    bot: CachedFetchBot = ctx.app

    await ctx.respond("Checking the database...")

    legacy_sources = await MirroredChannel.fetch_srcs(channel_id, legacy=True)
    new_style_sources = await MirroredChannel.fetch_srcs(channel_id, legacy=False)

    sources = {val: key for key, val in cfg.followables.items()}

    legacy_sources: str = [
        sources[legacy_source]
        if legacy_source in sources
        else f"Unknown Source: { legacy_source }"
        for legacy_source in legacy_sources
    ]
    new_style_sources: str = [
        sources[new_style_source]
        if new_style_source in sources
        else f"Unknown Source: { new_style_source }"
        for new_style_source in new_style_sources
    ]

    channel = await bot.fetch_channel(channel_id)
    channel_name = channel.name if channel else "Unknown Channel"

    await ctx.edit_last_response(
        "```\n"
        + f"Details for Channel: {channel_name} ({channel_id})\n"
        + "Legacy sources:\n"
        + ("\n".join(legacy_sources) if legacy_sources else "None")
        + "\n\n"
        + "New style sources:\n"
        + ("\n".join(new_style_sources) if new_style_sources else "None")
        + "\n"
        + "```"
    )


def register(bot: lb.BotApp):
    bot.listen(h.MessageCreateEvent)(message_create_repeater)
    bot.listen(h.MessageUpdateEvent)(message_update_repeater)
    bot.listen(h.MessageDeleteEvent)(message_delete_repeater)

    bot.command(mirror_group)
    bot.command(manual_mirror_send)
    bot.command(manual_mirror_update)
    bot.command(manual_mirror_delete)
    bot.command(mirror_cancel)
