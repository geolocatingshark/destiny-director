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
from random import randint
from time import perf_counter
from types import TracebackType
from typing import override

import dateparser
import hikari as h
import lightbulb as lb
import regex as re
from lightbulb import components as lbc

from ...common import cfg
from ...common.auth import owner_only
from ...common.bot import CachedFetchBot
from ...common.components import build_container
from ...common.schemas import MirroredChannel, MirroredMessage, ServerStatistics
from ...common.utils import (
    ErrorClass,
    classify_error,
    discord_error_logger,
    followable_name,
    format_duration,
    guild_scope,
    identity_for_exc,
    parse_channel_ref,
    reference_code,
)
from .. import utils
from ..mirror_core import (
    KernelFailure,
    KernelOutcome,
    KernelSuccess,
    KernelWorkControl,
    MirrorOperationType,
    build_reconcile_targets,
    kernel_work_control_registry,
    rate_limiter,
)

loader = lb.Loader()

re_markdown_link = re.compile(r"\[(.*?)\]\(.*?\)")

# Custom-id prefix for the per-progress-message "Cancel Mirror" button. The
# source_message_id is appended (``dd_mirror_cancel:<source_message_id>``) so the
# shared lightbulb-component router does not cross-fire between two concurrent
# progress messages — a correctness requirement.
_CANCEL_CUSTOM_ID_PREFIX = "dd_mirror_cancel"

# How long the cancel-button menu stays live (matches the old miru 7-hour window).
_CANCEL_MENU_TIMEOUT = 60 * 60 * 7


class TimedSemaphore(aio.Semaphore):
    """Semaphore to ensure no more than value requests per period are made

    This is to stay well within discord api rate limits while avoiding errors.

    Retained for ``mirror_tracing`` (which throttles its DB writes with one); the
    mirror fan-out itself now uses the token-bucket ``rate_limiter`` from
    ``mirror_core`` for global rate limiting plus a per-batch concurrency semaphore.
    """

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


# How often (seconds) the progress message is re-rendered while work continues.
_PROGRESS_UPDATE_INTERVAL = 5
# Max distinct reference codes listed in the failure breakdown of the progress UI.
_PROGRESS_MAX_BREAKDOWN = 5


def _status_footer(control: KernelWorkControl, *, final: bool) -> str:
    if not final:
        # ``cancelled`` is populated the moment cancel() fires, while in-flight
        # workers are still draining — surface that distinct state.
        return "🛑 Cancelling…" if control.cancelled else "⏳ In progress"
    base = "❌ Cancelled" if control.cancelled else "✅ Completed"
    return base + (" with errors" if control.failed_targets else "")


def render_mirror_progress(
    control: KernelWorkControl,
    *,
    title: str,
    source_message_link: str,
    source_message_summary: str,
    source_channel_link: str,
    source_channel_name: str,
    start_time: float,
    final: bool,
    enable_cancellation: bool,
) -> list[h.api.ComponentBuilder]:
    """Render the mirror progress as a Components V2 container.

    Re-rendered from scratch on each update (no magic field-index mutation). The
    cancel action row is included only when ``enable_cancellation`` and work is still
    in progress; its ``custom_id`` is namespaced by ``source_message_id`` so two
    concurrent progress messages never cross-fire.
    """
    elapsed = format_duration(perf_counter() - start_time)
    time_to_first_pass = elapsed if control.is_every_target_tried else "TBC"

    source_message_field = (
        f"[{source_message_summary}]({source_message_link})"
        if source_message_link
        else source_message_summary
    )
    source_channel_field = (
        f"[{source_channel_name}]({source_channel_link})"
        if source_channel_link
        else source_channel_name
    )

    sections = [
        f"## {title}",
        f"**Source message:** {source_message_field}\n"
        f"**Source channel:** {source_channel_field}",
        f"✅ Completed: **{len(control.successful_targets)}**\n"
        f"🔁 Retrying: **{len(control.targets_being_retried)}**\n"
        f"❌ Failed: **{len(control.failed_targets)}**\n"
        f"⏳ Remaining: **{len(control.targets_not_yet_tried)}**",
        f"Time taken: {elapsed}\nTime to try all channels once: {time_to_first_pass}",
    ]

    breakdown = control.failure_breakdown
    if breakdown:
        lines = [
            f"`{group.reference_code}` ×{group.count} "
            f"({group.error_class.name.lower()})"
            for group in breakdown[:_PROGRESS_MAX_BREAKDOWN]
        ]
        if len(breakdown) > _PROGRESS_MAX_BREAKDOWN:
            lines.append(f"…and {len(breakdown) - _PROGRESS_MAX_BREAKDOWN} more")
        sections.append("**Failure breakdown**\n" + "\n".join(lines))

    sections.append(_status_footer(control, final=final))

    container = build_container(
        sections,
        accent_color=cfg.embed_error_color
        if control.failed_targets
        else cfg.embed_default_color,
    )

    # Drop the cancel button once a cancel has been requested (control.cancelled) or
    # the run is finished, so it can't be pressed twice / after completion.
    if enable_cancellation and not final and not control.cancelled:
        container.add_action_row(
            [
                h.impl.InteractiveButtonBuilder(
                    style=h.ButtonStyle.DANGER,
                    custom_id=_cancel_custom_id(control.source_message_id),
                    label="Cancel Mirror",
                )
            ]
        )

    return [container]


def _cancel_custom_id(source_message_id: int) -> str:
    return f"{_CANCEL_CUSTOM_ID_PREFIX}:{source_message_id}"


def _build_cancel_menu(
    control: KernelWorkControl,
    client: lb.Client,
    render: collections.abc.Callable[[bool], list[h.api.ComponentBuilder]],
) -> tuple[lbc.Menu, lbc.MenuHandle]:
    """Build + attach a background lightbulb Menu that routes the cancel button.

    Mirrors the ``Paginator`` pattern: the menu exists purely as the custom-id ->
    callback router; the visually-identical button is rendered inside the CV2
    container by :func:`render_mirror_progress`. Returns the menu and its handle so
    the update loop can stop it once the run finishes.
    """
    menu = lbc.Menu()

    async def on_cancel(mctx: lbc.MenuContext) -> None:
        bot = t.cast(CachedFetchBot, mctx.client.app)
        if mctx.user.id not in await bot.fetch_owner_ids():
            # Only owners may cancel; acknowledge ephemerally for anyone else.
            await mctx.respond(
                "You are not allowed to cancel this mirror.", ephemeral=True
            )
            return
        # Graceful drain; the impl's post-run DB write persists successes-so-far so a
        # later edit reconciles correctly. Re-render immediately (the button drops out
        # now that control.cancelled is set) to acknowledge the interaction; the
        # background loop keeps updating until the in-flight workers finish.
        control.cancel()
        await mctx.respond(
            edit=True,
            flags=h.MessageFlag.IS_COMPONENTS_V2,
            components=render(False),
        )

    menu.add_interactive_button(
        h.ButtonStyle.DANGER,
        on_cancel,
        custom_id=_cancel_custom_id(control.source_message_id),
        label="Cancel Mirror",
    )
    handle = menu.attach_persistent(client, timeout=_CANCEL_MENU_TIMEOUT)
    return menu, handle


# The wrapped message listeners (@ignore_non_src_channels / @utils.ignore_own_user)
# strip lightbulb's DI, so a ``client: lb.Client = lb.di.INJECTED`` param on them never
# resolves — it stays the DI Marker, which has no ``safe_create_task`` and crashes
# ``Menu.attach_persistent``. Capture the real client once at startup via an unwrapped
# listener (DI works there, as in the autopost StartedEvent listeners) so the
# auto-mirror progress logger can still attach the cancel menu. Commands inject their
# own client directly and don't depend on this.
_client: lb.Client | None = None


@loader.listener(h.StartedEvent)
async def _capture_client(
    _event: h.StartedEvent, client: lb.Client = lb.di.INJECTED
) -> None:
    global _client
    _client = client


# Progress logging is best-effort and is awaited *before* the mirror runs, so an
# unbounded retry here would block the mirror entirely. Cap the attempts and give up.
_PROGRESS_LOGGER_MAX_TRIES = 5


async def start_progress_logger(
    bot: CachedFetchBot,
    control: KernelWorkControl,
    source_message: h.Message | None,
    start_time: float,
    *,
    title: str = "Mirror progress",
    source_channel: h.GuildChannel | None = None,
    enable_cancellation: bool = False,
    client: lb.Client | None = None,
) -> None:
    """Post the CV2 progress message and spawn the background update loop.

    Resolves the source links once, sends the initial container (attaching the
    lightbulb cancel menu when requested), then schedules ``_update_progress_loop``
    to re-render every few seconds until the run finishes.
    """
    tries = 0
    while True:
        try:
            log_channel = await bot.fetch_channel(cfg.log_channel)
            if not isinstance(log_channel, h.TextableGuildChannel):
                raise ValueError("Log channel must be a TextableGuildChannel")

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

            source_message_summary = (
                _get_message_summary(source_message) if source_message else "Unknown"
            )

            if source_channel:
                source_channel_link = (
                    "https://discord.com/channels/"
                    + str(source_channel.guild_id)
                    + "/"
                    + str(source_channel.id)
                )
                source_channel_name = source_channel.name or "Unknown"
            else:
                source_channel_link = ""
                source_channel_name = "Unknown"

            def render(
                final: bool,
                *,
                _scl=source_channel_link,
                _scn=source_channel_name,
                _sml=source_message_link,
                _sms=source_message_summary,
            ) -> list[h.api.ComponentBuilder]:
                return render_mirror_progress(
                    control,
                    title=title,
                    source_message_link=_sml,
                    source_message_summary=_sms,
                    source_channel_link=_scl,
                    source_channel_name=_scn,
                    start_time=start_time,
                    final=final,
                    enable_cancellation=enable_cancellation,
                )

            # The threaded client may be the unresolved DI Marker (wrapped listeners)
            # or None (direct calls); fall back to the captured real client. Only a
            # genuine lb.Client can back the cancel menu — never the Marker.
            cancel_client = client if isinstance(client, lb.Client) else _client
            menu_handle: lbc.MenuHandle | None = None
            if enable_cancellation and cancel_client is not None:
                _menu, menu_handle = _build_cancel_menu(control, cancel_client, render)

            log_message = await log_channel.send(
                components=render(not control.is_work_left_to_do),
                flags=h.MessageFlag.IS_COMPONENTS_V2,
            )
            break
        except Exception as e:
            e.add_note("Failed to log mirror progress due to exception\n")
            logging.exception(e)
            tries += 1
            if tries >= _PROGRESS_LOGGER_MAX_TRIES:
                # Give up rather than loop forever — the mirror itself is awaited
                # after this returns and must not be blocked by a progress-log fault.
                logging.error(
                    "Giving up on the mirror progress logger after %d attempts; "
                    "the mirror itself will still run.",
                    tries,
                )
                return
            await aio.sleep(min(60, 5 * (tries + 1)))

    aio.create_task(_update_progress_loop(log_message, control, render, menu_handle))


async def _update_progress_loop(
    log_message: h.Message,
    control: KernelWorkControl,
    render: collections.abc.Callable[[bool], list[h.api.ComponentBuilder]],
    menu_handle: "lbc.MenuHandle | None",
) -> None:
    """Re-render the progress container every few seconds until the run finishes.

    Re-renders from scratch (no magic field indices). On the final update it drops
    the cancel button (``final=True``) and stops the menu handle. Backs off linearly
    (capped at 60 s) on transient edit failures rather than the old ``5**tries``.
    """
    tries = 0
    while True:
        final_update = not control.is_work_left_to_do
        try:
            await log_message.edit(
                components=render(final_update),
                flags=h.MessageFlag.IS_COMPONENTS_V2,
            )
        except Exception as e:
            e.add_note("Failed to log mirror progress due to exception\n")
            logging.exception(e)
            tries += 1
            await aio.sleep(min(60, 5 * tries))
            continue

        if final_update:
            if menu_handle is not None:
                menu_handle.stop_interacting()
            return
        tries = 0
        await aio.sleep(_PROGRESS_UPDATE_INTERVAL)


# Logger whose records surface to the Discord alerts channel (ERROR/CRITICAL) via
# the root DiscordLogHandler, used for mirror-health escalations.
health_logger = logging.getLogger("dd.beacon.mirror.health")

# Escalate auto-disabled mirror counts: critical past the greater of these, error
# past the smaller pair, else just a console warning.
_DISABLE_CRITICAL_FRACTION = 0.10
_DISABLE_CRITICAL_MIN = 10
_DISABLE_ERROR_FRACTION = 0.05
_DISABLE_ERROR_MIN = 5


def _failure_summary(control: KernelWorkControl) -> str:
    """One-line-per-code summary of a run's failures for an alert message."""
    return "; ".join(
        f"{group.reference_code} ×{group.count} "
        f"({group.error_class.name.lower()}): {group.sample_message}"
        for group in control.failure_breakdown
    )


def flag_mirror_failure_ratio(control: KernelWorkControl) -> None:
    """Emit one aggregated alert per run summarising its failures.

    Replaces the old N-per-channel warnings with a single grouped, ref-coded record:

    - CRITICAL when a majority of a sufficiently-large run failed (pings owners via
      the Discord handler), enriched with the per-reference-code breakdown.
    - else ERROR when there were any permanent failures (so they surface without
      paging).
    - else nothing (transient blips that the retries absorbed).
    """
    total = control.total_targets
    failed = len(control.failed_targets)
    if not failed:
        return

    summary = _failure_summary(control)
    has_permanent = any(
        group.error_class is ErrorClass.PERMANENT for group in control.failure_breakdown
    )

    majority_fail = (
        total >= int(cfg.mirror_failure_min_sample)
        and failed / total >= cfg.mirror_failure_ratio_threshold
    )

    if majority_fail:
        health_logger.critical(
            "Majority of mirrors failing: %d/%d %s targets failed for source %s — %s",
            failed,
            total,
            control.mirror_operation_type.name.lower(),
            control.source_channel_id,
            summary,
        )
    elif has_permanent:
        health_logger.error(
            "%d/%d %s mirror targets failed (permanent) for source %s — %s",
            failed,
            total,
            control.mirror_operation_type.name.lower(),
            control.source_channel_id,
            summary,
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


def _kernel_failure(ch_id: int, exc: BaseException) -> KernelFailure:
    """Build a classified, ref-coded :class:`KernelFailure` and log it locally.

    The per-target failure is logged at WARNING (below the Discord alert threshold)
    so individual failures stay in the console/Railway logs while only the per-run
    aggregated summary (:func:`flag_mirror_failure_ratio`) escalates to Discord.
    """
    logging.warning(exc, exc_info=exc)
    return KernelFailure(
        channel_id=ch_id,
        exc=exc,
        error_class=classify_error(exc),
        reference_code=reference_code(identity_for_exc(exc)),
    )


async def _send_one(
    bot: CachedFetchBot,
    msg: h.Message,
    ch_id: int,
    role_ping_per_ch_id: collections.abc.Mapping[int, int | None],
) -> int:
    """Send ``msg`` to channel ``ch_id`` (rate-limited) and return the new msg id.

    Crossposts as non-fatal post-success work for announcement channels (the
    "already crossposted" 400 is treated as success/ignored). Shared by the SEND
    kernel and the reconcile kernel's send branch.
    """
    channel = await bot.fetch_channel(ch_id)
    if not isinstance(channel, h.TextableChannel):
        raise ValueError("Channel is not textable")

    msg_content = add_role_ping_to_msg(msg.content, ch_id, role_ping_per_ch_id)

    async with rate_limiter:
        # Note: components are no longer mirrored. This allows us to use buttons for
        # admin purposes in the main server.
        mirrored_msg = await channel.send(
            msg_content,
            attachments=msg.attachments,
            embeds=msg.embeds,
            role_mentions=True,
        )

    if isinstance(channel, h.GuildNewsChannel):
        # If the channel is a news channel then crosspost the message as well. This is
        # non-fatal: a crosspost failure does not fail the send (the message is sent).
        crosspost_backoff = 30
        for _ in range(3):
            try:
                async with rate_limiter:
                    await bot.rest.crosspost_message(ch_id, mirrored_msg.id)
            except Exception as e:
                if (
                    isinstance(e, h.BadRequestError)
                    and "This message has already been crossposted" in e.message
                ):
                    # Already crossposted -> treat as success and stop.
                    break
                e.add_note(
                    f"Failed to crosspost message in channel {ch_id} due to exception\n"
                )
                logging.warning(e, exc_info=e)
                await aio.sleep(crosspost_backoff)
                crosspost_backoff = crosspost_backoff * 2
            else:
                break

    return mirrored_msg.id


@loader.listener(h.MessageCreateEvent)
@ignore_non_src_channels
@utils.ignore_own_user
async def message_create_repeater(
    event: h.MessageCreateEvent, client: lb.Client = lb.di.INJECTED
):
    cached_bot = t.cast(CachedFetchBot, event.app)
    await message_create_repeater_impl(
        event.message,
        cached_bot,
        t.cast(
            h.TextableChannel,
            await cached_bot.fetch_channel(event.message.channel_id),
        ),
        client=client,
    )


async def message_create_repeater_impl(
    msg: h.Message,
    bot: CachedFetchBot,
    channel: h.TextableChannel,
    wait_for_crosspost: bool = True,
    client: lb.Client | None = None,
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

    async def kernel(ch_id: int, msg_id: int | None) -> KernelOutcome:
        # NOTE: msg is captured as a closure here.
        try:
            new_msg_id = await _send_one(bot, msg, ch_id, mirror_mention_ids)
        except Exception as e:
            return _kernel_failure(ch_id, e)
        return KernelSuccess(channel_id=ch_id, message_id=new_msg_id)

    control = KernelWorkControl(
        source_channel_id=channel.id,
        source_message_id=msg.id,
        targets={ch_id: None for ch_id in mirrors},
        role_ping_per_ch_id=mirror_mention_ids,
        mirror_operation_type=MirrorOperationType.SEND,
        kernel=kernel,
    )

    await start_progress_logger(
        bot,
        control,
        source_message=msg,
        start_time=mirror_start_time,
        title="Mirror send progress",
        enable_cancellation=True,
        client=client,
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
        # Record only the freshly-sent dest message pairs. For a SEND every success is
        # newly sent, but using ``newly_sent`` keeps this symmetric with the reconcile
        # path and is a no-op for already-recorded edits.
        MirroredMessage.add_msgs_in_batch(
            dest_msgs=list(control.newly_sent.values()),
            dest_channels=list(control.newly_sent.keys()),
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
async def message_update_repeater(
    event: h.MessageUpdateEvent, client: lb.Client = lb.di.INJECTED
):
    await message_update_repeater_impl(
        t.cast(h.Message, event.message),
        t.cast(CachedFetchBot, event.app),
        client=client,
    )


async def message_update_repeater_impl(
    msg: h.Message, bot: CachedFetchBot, client: lb.Client | None = None
):
    backoff_timer = 30
    while True:
        try:
            # Reconcile: bring every *desired* dest into sync with the source's
            # current content. Existing mirrored messages are edited; desired dests
            # that never received the message (e.g. a previously-cancelled send) get a
            # fresh send, so all destinations converge on the source.
            desired_dests = await MirroredChannel.fetch_dests(msg.channel_id)
            existing_pairs = await MirroredMessage.get_dest_msgs_and_channels(msg.id)
            existing = {
                channel_id: dest_msg_id for dest_msg_id, channel_id in existing_pairs
            }
            if not desired_dests and not existing:
                # Return if this message was not mirrored and has no desired dests
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

    # Reconcile target map: edit dests that have a mirrored message, fresh-send to
    # desired dests that are missing one, so all destinations converge on the source.
    targets = build_reconcile_targets(
        desired_dests, existing, source_channel_id=msg.channel_id
    )

    async def edit_one(ch_id: int, dest_msg_id: int) -> int:
        async with rate_limiter:
            dest_msg = await bot.fetch_message(ch_id, dest_msg_id)
        msg_content = add_role_ping_to_msg(msg.content, ch_id, mirror_mention_ids)
        async with rate_limiter:
            # Note: components are no longer mirrored.
            await dest_msg.edit(
                msg_content,
                attachments=msg.attachments,
                embeds=msg.embeds,
                role_mentions=True,
            )
        return dest_msg_id

    async def kernel(ch_id: int, msg_id: int | None) -> KernelOutcome:
        try:
            if msg_id is None:
                # Missing dest -> fresh send (reconcile to the source's state).
                new_msg_id = await _send_one(bot, msg, ch_id, mirror_mention_ids)
                return KernelSuccess(channel_id=ch_id, message_id=new_msg_id)
            # Existing dest -> edit in place.
            edited_id = await edit_one(ch_id, msg_id)
            return KernelSuccess(channel_id=ch_id, message_id=edited_id)
        except Exception as e:
            return _kernel_failure(ch_id, e)

    control = KernelWorkControl(
        source_channel_id=msg.channel_id,
        source_message_id=msg.id,
        targets=targets,
        mirror_operation_type=MirrorOperationType.UPDATE,
        role_ping_per_ch_id=mirror_mention_ids,
        retry_threshold=2,
        kernel=kernel,
    )

    await start_progress_logger(
        bot,
        control,
        source_message=msg,
        start_time=mirror_start_time,
        title="Mirror update progress",
        enable_cancellation=True,
        client=client,
    )

    await control.run_till_completion()

    flag_mirror_failure_ratio(control)

    # Record only the newly-sent pairs (dests reconciled via a fresh send); edited
    # dests already have their MirroredMessage pair.
    if control.newly_sent:
        try:
            await MirroredMessage.add_msgs_in_batch(
                dest_msgs=list(control.newly_sent.values()),
                dest_channels=list(control.newly_sent.keys()),
                source_msg=msg.id,
                source_channel=msg.channel_id,
            )
        except Exception as e:
            logging.error("Error recording reconciled mirror messages: %s", e)


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

    async def kernel(ch_id: int, msg_id: int | None) -> KernelOutcome:
        # ``msg_id`` is the recorded dest message; ch_id is the dest channel it was
        # recorded under. Both progress accounting and the success outcome key on
        # ``ch_id`` (the scheduling key) — not ``dest_msg.channel_id``, which can
        # diverge in thread/forum edge cases and would mis-record the completion.
        if msg_id is None:
            return _kernel_failure(
                ch_id, ValueError("Missing dest message id for delete")
            )
        try:
            async with rate_limiter:
                dest_msg = await bot.fetch_message(ch_id, msg_id)
            async with rate_limiter:
                await dest_msg.delete()
        except Exception as e:
            return _kernel_failure(ch_id, e)
        return KernelSuccess(channel_id=ch_id, message_id=msg_id)

    control = KernelWorkControl(
        source_channel_id=None,
        source_message_id=msg_id,
        targets={channel_id: dest_msg_id for dest_msg_id, channel_id in msgs_to_delete},
        mirror_operation_type=MirrorOperationType.DELETE,
        role_ping_per_ch_id={},
        retry_threshold=2,
        kernel=kernel,
    )

    await start_progress_logger(
        bot,
        control,
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
    async def invoke(
        self,
        ctx: lb.Context,
        bot: CachedFetchBot = lb.di.INJECTED,
        client: lb.Client = lb.di.INJECTED,
    ):
        initial = await ctx.respond("Mirroring message...", ephemeral=True)
        logging.info(f"Manually mirroring for channel id {self.target.channel_id}")
        await message_create_repeater_impl(
            self.target,
            bot,
            t.cast(h.TextableChannel, await bot.fetch_channel(ctx.channel_id)),
            wait_for_crosspost=False,
            client=client,
        )
        await ctx.edit_response(initial, "Mirrored message.")


class ManualMirrorUpdate(
    lb.MessageCommand,
    name="mirror_update",
    description="Manually update a mirrored message",
    hooks=[owner_only],
):
    @lb.invoke
    async def invoke(
        self,
        ctx: lb.Context,
        bot: CachedFetchBot = lb.di.INJECTED,
        client: lb.Client = lb.di.INJECTED,
    ):
        initial = await ctx.respond("Updating message...", ephemeral=True)
        logging.info(
            f"Manually updating mirrored message {self.target.id} "
            f" in channel id {self.target.channel_id}"
        )
        await message_update_repeater_impl(self.target, bot, client=client)
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
