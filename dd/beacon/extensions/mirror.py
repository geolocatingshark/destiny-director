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
import collections.abc
import logging
import typing as t
from collections import defaultdict
from enum import Enum
from random import randint
from time import perf_counter
from types import TracebackType
from typing import override

import dateparser
import hikari as h
import lightbulb as lb
import miru as m
import regex as re

from ...common import cfg
from ...common.auth import owner_only
from ...common.bot import CachedFetchBot
from ...common.schemas import MirroredChannel, MirroredMessage, ServerStatistics
from ...common.utils import (
    discord_error_logger,
    followable_name,
    guild_scope,
    parse_channel_ref,
)
from .. import utils

loader = lb.Loader()

re_markdown_link = re.compile(r"\[(.*?)\]\(.*?\)")


class TimedSemaphore(aio.Semaphore):
    """Semaphore to ensure no more than value requests per period are made

    This is to stay well within discord api rate limits while avoiding errors"""

    def __init__(self, value: int = 30, period: int = 1):
        super().__init__(value)
        self.period = period

    async def arelease(self) -> None:
        """Delay release until period has passed"""
        await aio.sleep(self.period)
        super().release()

    @override
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.arelease()


# Discord allows up to 50 REST requests/second per bot; cap concurrent mirror
# API calls a little below that to leave headroom for other traffic.
DISCORD_API_CONCURRENCY_LIMIT = 45
discord_api_semaphore = TimedSemaphore(value=DISCORD_API_CONCURRENCY_LIMIT)


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
        self._registry: dict[tuple[int | None, int], KernelWorkControl] = {}
        self._locks: dict[tuple[int | None, int], aio.Lock] = defaultdict(aio.Lock)

    def register(self, control: "KernelWorkControl"):
        """Register a KernelWorkControl instance"""
        key = (
            control.source_channel_id,
            control.source_message_id,
        )
        if key in self._registry:
            existing_control = self._registry[key]
            if existing_control.is_work_left_to_do:
                # Consider making this an async wait for the other task to finish
                # Remember to alter the code for whether we will block for all
                # operation types individually or cumulatively if you do make this
                # change
                raise ValueError(f"KernelWorkControl already registered for {key}")
        self._registry[key] = control

    def lock_source_message(self, control: "KernelWorkControl") -> aio.Lock:
        """Wait for the lock for a KernelWorkControl instance"""
        key = (
            control.source_channel_id,
            control.source_message_id,
        )
        return self._locks[key]

    def cancel(
        self,
        source_channel_id: int | None,
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

    source: dict[int, int] = {source_channel_id: source_message_id} with size 1
    targets: dict[int, int | None] = {target_channel_id: target_message_id}
    """

    def __init__(
        self,
        source: collections.abc.Mapping[int | None, int],
        targets: collections.abc.Mapping[int, int | None],
        mirror_operation_type: MirrorOperationType,
        retry_threshold: int = 3,
    ):
        self.retry_threshold = retry_threshold
        self.source_channel_id = source.keys().__iter__().__next__()
        self.source_message_id = source[self.source_channel_id]
        self.mirror_operation_type = mirror_operation_type
        self._targets = targets
        self._tries: dict[int, int] = {target_id: 0 for target_id in self._targets}
        self._completed_successfully: dict[int, int] = {}
        self._scheduled: dict[int, int | None] = {}
        self.cancelled: dict[int, int | None] = {}

    def _report_try(self, channel_id: int):
        self._tries[channel_id] += 1
        self._scheduled.pop(channel_id)

    def report_scheduled(self, channel_id: int, message_id: int | None = None):
        if channel_id in self._scheduled:
            e = ValueError(
                f"Target already scheduled. "
                f"source_message_id: {self.source_message_id} id:"
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
    def failed_targets(self) -> dict[int, int | None]:
        "IDs that have failed more than the retry threshold and will not be retried"
        return {
            channel_id: message_id
            for channel_id, message_id in self._targets.items()
            if self._tries[channel_id] >= self.retry_threshold
        }

    @property
    def successful_targets(self) -> dict[int, int]:
        return self._completed_successfully

    @property
    def total_targets(self) -> int:
        return len(self._targets)

    @property
    def _targets_tried_at_least_once(self) -> dict[int, int | None]:
        return {
            target: self._targets[target]
            for target, tries_ in self._tries.items()
            if tries_ > 0
        }

    @property
    def targets_not_yet_tried(self) -> dict[int, int | None]:
        return {
            target: self._targets[target]
            for target, tries_ in self._tries.items()
            if tries_ == 0
        }

    @property
    def is_every_target_tried(self) -> bool:
        return len(self._targets_tried_at_least_once) == len(self._targets)

    @property
    def targets_to_schedule(self) -> dict[int, int | None]:
        return {
            channel_id: message_id
            for channel_id, message_id in self._targets.items()
            if channel_id not in self._scheduled
            and channel_id not in self.failed_targets
            and channel_id not in self.successful_targets
            and channel_id not in self.cancelled
        }

    @property
    def targets_being_retried(self) -> dict[int, int | None]:
        return {
            channel_id: message_id
            for channel_id, message_id in self._targets.items()
            if channel_id in self._targets_tried_at_least_once
            and channel_id not in self.failed_targets
            and channel_id not in self.successful_targets
        }

    @property
    def is_work_left_to_do(self) -> bool:
        return bool(self.targets_to_schedule) or bool(self._scheduled)


class KernelWorkControl(KernelWorkTracker):
    def __init__(
        self,  #
        source: collections.abc.Mapping[int | None, int],
        targets: collections.abc.Mapping[int, int | None],
        role_ping_per_ch_id: collections.abc.Mapping[int, int | None],
        mirror_operation_type: MirrorOperationType,
        kernel: collections.abc.Callable[..., t.Any],
        retry_threshold: int = 3,
    ):
        super().__init__(
            source,
            targets,
            mirror_operation_type=mirror_operation_type,
            retry_threshold=retry_threshold,
        )
        self.role_ping_per_ch_id = role_ping_per_ch_id
        # The kernel is supplied at construction time and is expected to have the
        # signature ``async _kernel(self, ch_id: int, msg_id: int, delay: int = 0)``.
        self._kernel: collections.abc.Callable[
            ..., collections.abc.Coroutine[t.Any, t.Any, None]
        ] = kernel
        self._tasks: set[aio.Task[None]] = set()

    async def run_till_completion(self):
        async with kernel_work_control_registry.lock_source_message(self):
            kernel_work_control_registry.register(self)

            loop_number = 0
            while self.is_work_left_to_do and loop_number < self.retry_threshold:
                tasks = [
                    aio.create_task(
                        self._kernel(
                            self,
                            dest_ch_id,
                            dest_msg_id,
                            # First pass runs immediately; retries wait a
                            # randomised 3-5 minutes to spread out load.
                            delay=0 if loop_number == 0 else randint(180, 300),
                        )
                    )
                    for dest_ch_id, dest_msg_id in self.targets_to_schedule.items()
                ]
                self._tasks.update(tasks)
                await aio.wait(self._tasks, return_when=aio.ALL_COMPLETED)
                loop_number += 1

    def cancel(self):
        for task in self._tasks:
            task: aio.Task[None]
            task.cancel()

        self.cancelled.update(self._scheduled)
        self.cancelled.update(self.targets_to_schedule)
        self._scheduled.clear()


def _get_message_summary(msg: h.Message, default: str = "Link") -> str:
    # Prefer the first line of the message content; fall back to the first embed
    # title/description when the message has no text content.
    summary = ""
    if msg.content:
        summary = msg.content.split("\n")[0]

    if not summary:
        for embed in msg.embeds:
            if embed.title:
                summary = embed.title
                break
            if embed.description:
                summary = embed.description.split("\n")[0]
                break

    if not summary:
        return default

    summary = summary.replace("*", "")
    summary = summary.replace("_", "")
    summary = summary.replace("#", "")
    summary = summary.strip("{}")
    summary = summary.strip("<>")
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
                    else (
                        f"{time_taken // 60} minutes "
                        f"{round(time_taken % 60, 2)} seconds"
                    )
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
    def __init__(self, callback: collections.abc.Callable[..., t.Any] | None = None):
        super().__init__(style=h.ButtonStyle.DANGER, label="Cancel Mirror")
        self._callback = callback

    @override
    async def callback(self, context: m.ViewContext):
        if self._callback:
            await self._callback(self, context)


async def log_mirror_progress_to_discord(
    bot: CachedFetchBot,
    control: KernelWorkControl,
    source_message: h.Message | None,
    start_time: float,
    title: str | None = "Mirror progress",
    source_channel: h.GuildChannel | None = None,
    enable_cancellation: bool | None = False,
):
    tries = 0
    while True:
        try:
            log_channel = await bot.fetch_channel(cfg.log_channel)
            if not isinstance(log_channel, h.TextableGuildChannel):
                raise ValueError("Log channel must be a TextableGuildChannel")

            time_taken = round(perf_counter() - start_time, 2)
            time_taken = (
                f"{time_taken} seconds"
                if time_taken < 60
                else f"{time_taken // 60} minutes {round(time_taken % 60, 2)} seconds"
            )

            if not source_channel and source_message:
                channel_from_message = await bot.fetch_channel(
                    source_message.channel_id
                )
                if isinstance(channel_from_message, h.GuildChannel):
                    source_channel = channel_from_message

            if source_channel and source_message:
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
            else:
                source_channel_link = ""

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
                elif (
                    source_message.attachments
                    and source_message.attachments[0].media_type
                    and source_message.attachments[0].media_type.startswith("image")
                ):
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
                    bot = t.cast(CachedFetchBot, ctx.bot)
                    if ctx.user.id not in await bot.fetch_owner_ids():
                        # Ignore non owners
                        return

                    control.cancel()
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


# Logger whose records surface to the Discord alerts channel (ERROR/CRITICAL) via
# the root DiscordLogHandler, used for mirror-health escalations.
health_logger = logging.getLogger("dd.beacon.mirror.health")

# Escalate auto-disabled mirror counts: critical past the greater of these, error
# past the smaller pair, else just a console warning.
_DISABLE_CRITICAL_FRACTION = 0.10
_DISABLE_CRITICAL_MIN = 10
_DISABLE_ERROR_FRACTION = 0.05
_DISABLE_ERROR_MIN = 5


def _log_kernel_failure(exc: BaseException) -> None:
    """Log a per-target kernel failure for local diagnostics only.

    Deliberately below the alert threshold (WARNING, not ERROR) so individual
    target failures stay in the Railway/console logs but do **not** reach the
    Discord alert handler or feed storm promotion — only the per-run
    majority-failing summary (:func:`flag_mirror_failure_ratio`) escalates to
    Discord. (The kernel system is slated for rework; keeping this minimal.)
    """
    logging.warning(exc, exc_info=exc)


def flag_mirror_failure_ratio(control: KernelWorkControl) -> None:
    """Emit a CRITICAL log when most of a mirror run's targets failed.

    Routed through ``logging`` so the Discord alert handler renders it and pings
    owners (CRITICAL). A minimum-sample guard stops a single 1/1 failure — or any
    tiny run — from paging anyone.
    """
    total = control.total_targets
    if total < int(cfg.mirror_failure_min_sample):
        return
    failed = len(control.failed_targets)
    if failed / total < cfg.mirror_failure_ratio_threshold:
        return
    health_logger.critical(
        "Majority of mirrors failing: %d/%d %s targets failed for source %s",
        failed,
        total,
        control.mirror_operation_type.name.lower(),
        control.source_channel_id,
    )


def ignore_non_src_channels(func: collections.abc.Callable[..., t.Any]):
    async def wrapped_func(event: h.MessageEvent):
        msg = None
        if isinstance(event, (h.MessageCreateEvent, h.MessageUpdateEvent)):
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
    msg: h.Message,
    bot: CachedFetchBot,
    channel: h.TextableChannel,
    wait_for_crosspost: bool,
):
    backoff_timer = 30
    while True:
        try:
            channel_name_or_id = str(followable_name(id=channel.id))
            logging.info(
                f"MessageCreateEvent received for message in channel: "
                f"{channel_name_or_id}"
            )

            # The below is to make sure we aren't using a reference to a message that
            # has already changed (in particular, has already been crossposted)
            # Using such a reference would result in us waiting forever for a crosspost
            # event that has already fired
            msg = await bot.rest.fetch_message(msg.channel_id, msg.id)

            if wait_for_crosspost and h.MessageFlag.CROSSPOSTED not in msg.flags:
                logging.info(
                    f"Message in channel {channel_name_or_id} not crossposted, "
                    "waiting..."
                )
                await bot.wait_for(
                    h.MessageUpdateEvent,
                    # Wait up to 12 hours for the source message to be crossposted.
                    timeout=12 * 60 * 60,
                    predicate=lambda e, msg=msg: bool(
                        e.message.id == msg.id
                        and e.message.flags
                        and h.MessageFlag.CROSSPOSTED in e.message.flags
                    ),
                )
                logging.info(
                    f"Crosspost event received for message in channel "
                    f"{channel_name_or_id}, " + "continuing..."
                )
        except TimeoutError:
            return
        except Exception as e:
            await discord_error_logger(e, operation="Mirror crosspost")
            await aio.sleep(backoff_timer)
            backoff_timer += 30 / backoff_timer
        else:
            break


def add_role_ping_to_msg(
    msg_content: str | None,
    dest_channel_id: int,
    role_ping_per_ch_id: collections.abc.Mapping[int, int | None],
) -> str | None:
    """Add role ping to message content if specified for this channel

    In case the roll ping is 0, no changes are made."""
    role_ping = int(role_ping_per_ch_id.get(dest_channel_id) or 0)
    if role_ping:
        msg_content = msg_content.strip("\n") + "\n\n" if msg_content else ""
        msg_content += f"||<@&{role_ping}>||"
    return msg_content


@loader.listener(h.MessageCreateEvent)
@ignore_non_src_channels
@utils.ignore_own_user
async def message_create_repeater(event: h.MessageCreateEvent):
    cached_bot = t.cast(CachedFetchBot, event.app)
    await message_create_repeater_impl(
        event.message,
        cached_bot,
        t.cast(
            h.TextableChannel,
            await cached_bot.fetch_channel(event.message.channel_id),
        ),
    )


async def message_create_repeater_impl(
    msg: h.Message,
    bot: CachedFetchBot,
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
        msg_id: int | None = None,
        delay: int = 0,
    ):
        # NOTE: msg is passed as a closure to the kernel function here
        control.report_scheduled(ch_id)
        await aio.sleep(delay)

        try:
            channel = await bot.fetch_channel(ch_id)

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
            _log_kernel_failure(e)
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
                    _log_kernel_failure(e)
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

    flag_mirror_failure_ratio(control)

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
    else:
        disabled_mirrors = ""

    if disabled_mirrors:
        num_disabled = len(disabled_mirrors)
        total = control.total_targets
        message = (
            ("Disabled " if cfg.disable_bad_channels else "Would disable ")
            + str(num_disabled)
            + f" mirrors (of {total} targets this run): "
            + ", ".join([f"{mirror[0]}: {mirror[1]}" for mirror in disabled_mirrors])
        )
        # Escalate by how big a share of this run's targets got disabled: critical
        # past max(10%, 10), error past max(5%, 5), else just a console warning.
        if num_disabled > max(
            _DISABLE_CRITICAL_MIN, _DISABLE_CRITICAL_FRACTION * total
        ):
            health_logger.critical(message)
        elif num_disabled > max(_DISABLE_ERROR_MIN, _DISABLE_ERROR_FRACTION * total):
            health_logger.error(message)
        else:
            health_logger.warning(message)


@loader.listener(h.MessageUpdateEvent)
@ignore_non_src_channels
@utils.ignore_own_user
async def message_update_repeater(event: h.MessageUpdateEvent):
    await message_update_repeater_impl(
        t.cast(h.Message, event.message),
        t.cast(CachedFetchBot, event.app),
    )


async def message_update_repeater_impl(msg: h.Message, bot: CachedFetchBot):
    backoff_timer = 30
    while True:
        try:
            msgs_to_update = await MirroredMessage.get_dest_msgs_and_channels(msg.id)
            if not msgs_to_update:
                # Return if this message was not mirrored for any reason
                return
            mirror_mention_ids = await MirroredChannel.fetch_mirror_and_role_mention_id(
                msg.channel_id
            )

        except Exception as e:
            await discord_error_logger(e, operation="Mirror update")
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
        msg_id: int | None,
        delay: int = 0,
    ):
        if msg_id is None:
            raise ValueError("msg_id should not be None for message_update_repeater")
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
            _log_kernel_failure(e)
            control.report_failure(ch_id)
        else:
            control.report_completed(ch_id, msg_id)

    control = KernelWorkControl(
        source={msg.channel_id: msg.id},
        targets={channel_id: dest_msg_id for dest_msg_id, channel_id in msgs_to_update},
        mirror_operation_type=MirrorOperationType.UPDATE,
        role_ping_per_ch_id=mirror_mention_ids,
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

    flag_mirror_failure_ratio(control)


@loader.listener(h.MessageDeleteEvent)
@ignore_non_src_channels
async def message_delete_repeater(event: h.MessageDeleteEvent):
    msg_id = event.message_id
    msg = event.old_message
    cached_bot = t.cast(CachedFetchBot, event.app)

    await message_delete_repeater_impl(msg_id, msg, cached_bot)


async def message_delete_repeater_impl(
    msg_id: int, msg: h.Message | None, bot: CachedFetchBot
):
    backoff_timer = 30
    while True:
        try:
            msgs_to_delete = await MirroredMessage.get_dest_msgs_and_channels(msg_id)
            if not msgs_to_delete:
                # Return if this message was not mirrored for any reason
                return

        except Exception as e:
            await discord_error_logger(e, operation="Mirror delete")
            await aio.sleep(backoff_timer)
            backoff_timer += 30 / backoff_timer
        else:
            break

    mirror_start_time = perf_counter()

    async def kernel(
        tracker: KernelWorkControl,
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
            _log_kernel_failure(e)
            tracker.report_failure(ch_id)
        else:
            tracker.report_completed(dest_msg.channel_id, msg_id)

    control = KernelWorkControl(
        source={None: msg_id},
        targets={channel_id: dest_msg_id for dest_msg_id, channel_id in msgs_to_delete},
        mirror_operation_type=MirrorOperationType.DELETE,
        role_ping_per_ch_id={},
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

    flag_mirror_failure_ratio(control)


@loader.task(lb.uniformtrigger(hours=24 * 7, wait_first=False), max_failures=-1)
async def refresh_server_sizes(bot: CachedFetchBot = lb.di.INJECTED):
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

            await discord_error_logger(e, operation="Server size refresh")

            if not should_retry_:
                break

            await aio.sleep(backoff_timer * 60)
            backoff_timer = backoff_timer * 4

        else:
            break


@loader.task(lb.uniformtrigger(hours=24, wait_first=False), max_failures=-1)
async def prune_message_db(bot: CachedFetchBot = lb.di.INJECTED):
    await aio.sleep(randint(120, 1800))
    try:
        await MirroredMessage.prune()
    except Exception as e:
        e.add_note("Exception during routine pruning of MirroredMessage")
        await discord_error_logger(e, operation="Mirror DB prune")


# Command group for all mirror commands
mirror_group = lb.Group(
    "mirror",
    "Command group for all mirror control/administration commands",
)


@mirror_group.register
class UndoAutoDisable(
    lb.SlashCommand,
    name="undo_auto_disable",
    description="Undo auto disable of a channel due to repeated post failures",
    hooks=[owner_only],
):
    from_date = lb.string("from_date", "Date to start from")

    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        await ctx.defer()

        from_date = dateparser.parse(self.from_date)

        mirrors = await MirroredChannel.undo_auto_disable_for_failure(since=from_date)
        response = f"Undid auto disable since {from_date} for channels {mirrors}"
        logging.info(response)
        await ctx.respond(response)


@mirror_group.register
class ManualAdd(
    lb.SlashCommand,
    name="manual_add",
    description="Manually add a mirror to the database",
    hooks=[owner_only],
):
    src = lb.string("src", "Source channel (link, mention, or id)")
    dest = lb.string("dest", "Destination channel (link, mention, or id)")
    dest_server_id = lb.string(
        "dest_server_id",
        "Destination server id (optional if dest is a channel link)",
        default="",
    )

    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        await ctx.defer()

        try:
            src, _ = parse_channel_ref(self.src)
            dest, dest_guild_id = parse_channel_ref(self.dest)
        except ValueError as e:
            await ctx.respond(str(e))
            return

        if self.dest_server_id.strip():
            try:
                dest_server_id = int(self.dest_server_id.strip())
            except ValueError:
                await ctx.respond(f"{self.dest_server_id!r} is not a valid server id")
                return
        elif dest_guild_id is not None:
            dest_server_id = dest_guild_id
        else:
            await ctx.respond(
                "Provide dest_server_id, or pass dest as a full channel link "
                "(which includes the server id)."
            )
            return

        await MirroredChannel.add_mirror(
            src, dest, dest_server_id=dest_server_id, legacy=True
        )
        await ctx.respond(f"Added mirror {src} -> {dest} (server {dest_server_id})")


@mirror_group.register
class ManualMirrorDelete(
    lb.SlashCommand,
    name="delete_msg",
    description="Manually delete a mirrored message",
    hooks=[owner_only],
):
    message_id = lb.string("message_id", "Message to delete")

    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        mid = int(self.message_id)

        initial = await ctx.respond("Deleting message...", ephemeral=True)
        logging.info(f"Manually deleting mirrored message {mid}")
        await message_delete_repeater_impl(mid, bot.cache.get_message(mid), bot)
        await ctx.edit_response(initial, "Deleted messages.")


@mirror_group.register
class MirrorSourceDetails(
    lb.SlashCommand,
    name="source_details",
    description="Show details about a channels mirror sources if any",
    hooks=[owner_only],
):
    channel_id = lb.string(
        "channel_id", "Destination channel to show details of (link, mention, or id)"
    )

    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        try:
            channel_id, _ = parse_channel_ref(self.channel_id)
        except ValueError as e:
            await ctx.respond(str(e))
            return

        initial = await ctx.respond("Checking the database...")

        legacy_sources = await MirroredChannel.fetch_srcs(channel_id, legacy=True)
        new_style_sources = await MirroredChannel.fetch_srcs(channel_id, legacy=False)

        sources = {val: key for key, val in cfg.followables.items()}

        legacy_sources = [
            sources.get(legacy_source, f"Unknown Source: {legacy_source}")
            for legacy_source in legacy_sources
        ]
        new_style_sources = [
            sources.get(new_style_source, f"Unknown Source: {new_style_source}")
            for new_style_source in new_style_sources
        ]

        channel = await bot.fetch_channel(channel_id)
        channel_name = channel.name if channel else "Unknown Channel"

        await ctx.edit_response(
            initial,
            "```\n"
            + f"Details for Channel: {channel_name} ({channel_id})\n"
            + "Legacy sources:\n"
            + ("\n".join(legacy_sources) if legacy_sources else "None")
            + "\n\n"
            + "New style sources:\n"
            + ("\n".join(new_style_sources) if new_style_sources else "None")
            + "\n"
            + "```",
        )


class ManualMirrorSend(
    lb.MessageCommand,
    name="mirror_send",
    description="Manually mirror a message",
    hooks=[owner_only],
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        initial = await ctx.respond("Mirroring message...", ephemeral=True)
        logging.info(f"Manually mirroring for channel id {self.target.channel_id}")
        await message_create_repeater_impl(
            self.target,
            bot,
            t.cast(h.TextableChannel, await bot.fetch_channel(ctx.channel_id)),
            wait_for_crosspost=False,
        )
        await ctx.edit_response(initial, "Mirrored message.")


class ManualMirrorUpdate(
    lb.MessageCommand,
    name="mirror_update",
    description="Manually update a mirrored message",
    hooks=[owner_only],
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        initial = await ctx.respond("Updating message...", ephemeral=True)
        logging.info(
            f"Manually updating mirrored message {self.target.id} "
            f" in channel id {self.target.channel_id}"
        )
        await message_update_repeater_impl(self.target, bot)
        await ctx.edit_response(initial, "Updated message.")


class MirrorCancel(
    lb.MessageCommand,
    name="mirror_cancel",
    description="Manually cancels a message mirror currently in progress",
    hooks=[owner_only],
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        message: h.Message = self.target
        try:
            kernel_work_control_registry.cancel(message.channel_id, message.id)
        except ValueError as e:
            await ctx.respond("Failed to cancel mirror: " + str(e), ephemeral=True)
            return
        else:
            await ctx.respond("Cancelled mirror", ephemeral=True)


loader.command(
    mirror_group, guilds=guild_scope(*cfg.test_env, cfg.control_discord_server_id)
)
loader.command(
    ManualMirrorSend,
    guilds=guild_scope(
        *cfg.test_env, cfg.control_discord_server_id, cfg.kyber_discord_server_id
    ),
)
loader.command(
    ManualMirrorUpdate,
    guilds=guild_scope(
        *cfg.test_env, cfg.control_discord_server_id, cfg.kyber_discord_server_id
    ),
)
loader.command(
    MirrorCancel,
    guilds=guild_scope(
        *cfg.test_env, cfg.control_discord_server_id, cfg.kyber_discord_server_id
    ),
)
