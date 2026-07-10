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

"""Mirror subsystem gateway surface: thin enqueue handlers, the progress UI, and the
admin commands, layered over the durable ``mirror_delivery`` ledger.

The Discord fan-out itself lives in :mod:`dd.beacon.mirror_worker` (the convergence
worker). This module's listeners do one transactional enqueue each (send/edit/delete),
register a :class:`RunView`, post a Components V2 progress card, and nudge the worker.
The worker records outcomes back into the view and fires :func:`_run_end_hook` on
completion (failure-ratio alert, auto-disable sweep, run summary).
"""

import asyncio as aio
import collections.abc
import contextlib
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
from ...common.schemas import MirrorDelivery, MirroredChannel, ServerStatistics
from ...common.utils import (
    ErrorClass,
    classify_error,
    discord_error_logger,
    followable_name,
    format_duration,
    guild_scope,
    parse_channel_ref,
)
from .. import utils
from ..mirror_core import MirrorOperationType, RunView
from ..mirror_worker import mirror_worker

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


def _status_footer(view: RunView, *, final: bool) -> str:
    if not final:
        # ``cancel_requested`` is set the moment cancel fires, while in-flight workers
        # are still draining — surface that distinct state. An edit that supersedes an
        # in-flight fan-out reads as a handover, not a plain cancel.
        if view.cancel_requested:
            return (
                "♻️ Superseding with edit…"
                if view.superseded_by_edit
                else "🛑 Cancelling…"
            )
        return "⏳ In progress"
    if view.superseded_by_edit:
        base = "♻️ Superseded by edit"
    elif view.cancel_requested:
        base = "❌ Cancelled"
    else:
        base = "✅ Completed"
    return base + (" with errors" if view.failed else "")


def _progress_bar(view: RunView, width: int = 12) -> str:
    """A single stacked bar of the run's state (done / retrying / failed / left).

    Segment widths are proportional to ``total``; blanks absorb both rounding and
    in-flight targets so the bar never overflows. The trailing percentage is the
    *resolved* fraction (delivered + failed) — targets in a terminal state;
    retrying/remaining are still pending.
    """
    total = view.total or 1
    done = view.delivered
    retry = view.retrying
    fail = view.failed

    seg_done = round(done / total * width)
    seg_retry = min(round(retry / total * width), width - seg_done)
    seg_fail = min(round(fail / total * width), width - seg_done - seg_retry)
    seg_blank = width - seg_done - seg_retry - seg_fail

    bar = "🟩" * seg_done + "🟨" * seg_retry + "🟥" * seg_fail + "⬜" * seg_blank
    pct = round((done + fail) / total * 100)
    return f"{bar}  {pct}%"


def _throughput_line(view: RunView, elapsed_secs: float) -> str | None:
    """Rate + ETA derived from resolved targets over elapsed time.

    Returns ``None`` until at least one target has resolved (the rate is meaningless
    before then) so early renders stay uncluttered. Once every target is resolved the
    ETA is dropped and only the throughput remains.
    """
    resolved = view.throughput_resolved
    if elapsed_secs <= 0 or resolved == 0:
        return None
    rate = resolved / elapsed_secs
    remaining = view.total - resolved
    if remaining <= 0:
        return f"Throughput: {rate:.1f} channels/sec"
    return (
        f"Throughput: {rate:.1f} channels/sec · "
        f"ETA ~{format_duration(remaining / rate)}"
    )


def render_mirror_progress(
    view: RunView,
    *,
    title: str,
    source_message_link: str,
    source_message_summary: str,
    source_channel_link: str,
    source_channel_name: str,
    final: bool,
    enable_cancellation: bool,
) -> list[h.api.ComponentBuilder]:
    """Render the mirror progress as a Components V2 container.

    Re-rendered from scratch on each update. The cancel action row is included only when
    ``enable_cancellation`` and work is still in progress; its ``custom_id`` is
    namespaced by ``src_msg_id`` so two concurrent progress messages never cross-fire.
    """
    elapsed_secs = perf_counter() - view.start_time
    elapsed = format_duration(elapsed_secs)

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
        # Wrapped in a code block so Discord renders it monospace: the bar cells line
        # up and the colour squares stay distinct instead of being squashed together.
        "```\n"
        f"{_progress_bar(view)}\n"
        f"🟩 {'Completed':<9} {view.delivered}\n"
        f"🟨 {'Retrying':<9} {view.retrying}\n"
        f"🟥 {'Failed':<9} {view.failed}\n"
        f"⬜ {'Remaining':<9} {view.not_yet_tried}\n"
        "```",
        f"Time taken: {elapsed}",
    ]

    throughput = _throughput_line(view, elapsed_secs)
    if throughput:
        sections[-1] += "\n" + throughput

    breakdown = view.failure_breakdown
    if breakdown:
        lines = [
            f"`{group.reference_code}` ×{group.count} "
            f"({group.error_class.name.lower()})"
            for group in breakdown[:_PROGRESS_MAX_BREAKDOWN]
        ]
        if len(breakdown) > _PROGRESS_MAX_BREAKDOWN:
            lines.append(f"…and {len(breakdown) - _PROGRESS_MAX_BREAKDOWN} more")
        sections.append("**Failure breakdown**\n" + "\n".join(lines))

    # NEW: how many mirrors the run-end sweep disabled (only on the final render, and
    # only when something was actually disabled).
    if final and view.disabled_count:
        sections.append(f"Disabled channels: {view.disabled_count}")

    sections.append(_status_footer(view, final=final))

    container = build_container(
        sections,
        accent_color=cfg.embed_error_color if view.failed else cfg.embed_default_color,
    )

    # Drop the cancel button once a cancel has been requested or the run is finished, so
    # it can't be pressed twice / after completion.
    if enable_cancellation and not final and not view.cancel_requested:
        container.add_action_row(
            [
                h.impl.InteractiveButtonBuilder(
                    style=h.ButtonStyle.DANGER,
                    custom_id=_cancel_custom_id(view.src_msg_id),
                    label="Cancel Mirror",
                )
            ]
        )

    return [container]


def _cancel_custom_id(source_message_id: int) -> str:
    return f"{_CANCEL_CUSTOM_ID_PREFIX}:{source_message_id}"


async def _cancel_run(view: RunView) -> None:
    """Cancel a run's not-yet-delivered destinations and drive completion.

    Cancels PENDING rows in the ledger, marks the requested flag (so claimed-but-
    unstarted rows short-circuit in the worker), records the cancelled dests, and
    nudges the worker. In-flight Discord calls drain and flush normally.
    """
    cancelled = await MirrorDelivery.cancel_pending(view.src_msg_id)
    view.cancel_requested = True
    for dest in cancelled:
        view.on_cancelled(dest)
    mirror_worker.maybe_finalize(view)
    mirror_worker.nudge()


def _build_cancel_menu(
    view: RunView,
    client: lb.Client,
    render: collections.abc.Callable[[bool], list[h.api.ComponentBuilder]],
) -> tuple[lbc.Menu, lbc.MenuHandle]:
    """Build + attach a background lightbulb Menu that routes the cancel button.

    Mirrors the ``Paginator`` pattern: the menu exists purely as the custom-id ->
    callback router; the visually-identical button is rendered inside the CV2 container
    by :func:`render_mirror_progress`.
    """
    menu = lbc.Menu()

    async def on_cancel(mctx: lbc.MenuContext) -> None:
        bot = t.cast(CachedFetchBot, mctx.client.app)
        if mctx.user.id not in await bot.fetch_owner_ids():
            await mctx.respond(
                "You are not allowed to cancel this mirror.", ephemeral=True
            )
            return
        await _cancel_run(view)
        # Re-render immediately (the button drops out now that cancel_requested is set)
        # to acknowledge the interaction; the background loop keeps updating.
        await mctx.respond(
            edit=True,
            flags=h.MessageFlag.IS_COMPONENTS_V2,
            components=render(False),
        )

    menu.add_interactive_button(
        h.ButtonStyle.DANGER,
        on_cancel,
        custom_id=_cancel_custom_id(view.src_msg_id),
        label="Cancel Mirror",
    )
    handle = menu.attach_persistent(client, timeout=_CANCEL_MENU_TIMEOUT)
    return menu, handle


# The wrapped message listeners (@ignore_non_src_channels / @utils.ignore_own_user)
# strip lightbulb's DI, so a ``client: lb.Client = lb.di.INJECTED`` param on them never
# resolves. Capture the real client once at startup via an unwrapped listener so the
# auto-mirror progress logger can still attach the cancel menu.
_client: lb.Client | None = None


# Progress logging is best-effort; cap the attempts and give up rather than loop.
_PROGRESS_LOGGER_MAX_TRIES = 5


async def start_progress_logger(
    bot: CachedFetchBot,
    view: RunView,
    *,
    source_message: h.Message | None = None,
    source_channel: h.GuildChannel | None = None,
    title: str = "Mirror progress",
    enable_cancellation: bool = False,
    client: lb.Client | None = None,
) -> None:
    """Post the CV2 progress message and spawn the background update loop.

    Resolves the source links once (best-effort — ``source_message`` may be ``None`` for
    a recovery card), sends the initial container (attaching the cancel menu when
    requested), then schedules :func:`_update_progress_loop`.
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
                    view,
                    title=title,
                    source_message_link=_sml,
                    source_message_summary=_sms,
                    source_channel_link=_scl,
                    source_channel_name=_scn,
                    final=final,
                    enable_cancellation=enable_cancellation,
                )

            # The threaded client may be the unresolved DI Marker (wrapped listeners) or
            # None (direct calls); fall back to the captured real client.
            cancel_client = client if isinstance(client, lb.Client) else _client
            menu_handle: lbc.MenuHandle | None = None
            if enable_cancellation and cancel_client is not None:
                _menu, menu_handle = _build_cancel_menu(view, cancel_client, render)

            log_message = await log_channel.send(
                components=render(view.finalized),
                flags=h.MessageFlag.IS_COMPONENTS_V2,
            )
            break
        except Exception as e:
            e.add_note("Failed to log mirror progress due to exception\n")
            logging.exception(e)
            tries += 1
            if tries >= _PROGRESS_LOGGER_MAX_TRIES:
                logging.error(
                    "Giving up on the mirror progress logger after %d attempts; "
                    "the mirror itself will still run.",
                    tries,
                )
                return
            await aio.sleep(min(60, 5 * (tries + 1)))

    aio.create_task(_update_progress_loop(log_message, view, render, menu_handle))


async def _update_progress_loop(
    log_message: h.Message,
    view: RunView,
    render: collections.abc.Callable[[bool], list[h.api.ComponentBuilder]],
    menu_handle: "lbc.MenuHandle | None",
) -> None:
    """Re-render the progress container every few seconds until the run is finalized.

    ``finalized`` (not merely ``is_complete``) gates the final render, so the run-end
    hook has run and ``disabled_count`` is populated before the card freezes. On the
    final update it drops the cancel button, stops the menu handle, and evicts the view.
    """
    tries = 0
    while True:
        final_update = view.finalized
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
            mirror_worker.evict(view)
            return
        tries = 0
        await aio.sleep(_PROGRESS_UPDATE_INTERVAL)


# Logger whose records surface to the Discord alerts channel (ERROR/CRITICAL) via the
# root DiscordLogHandler, used for mirror-health escalations.
health_logger = logging.getLogger("dd.beacon.mirror.health")

# Escalate a global auto-disable sweep by its blast radius (count of mirrors disabled):
# critical past the first, error past the second, else just a console warning.
_DISABLE_CRITICAL_MIN = 10
_DISABLE_ERROR_MIN = 5


def _failure_summary(view: RunView) -> str:
    """One-line-per-code summary of a run's failures for an alert message."""
    return "; ".join(
        f"{group.reference_code} ×{group.count} "
        f"({group.error_class.name.lower()}): {group.sample_message}"
        for group in view.failure_breakdown
    )


def flag_mirror_failure_ratio(view: RunView) -> None:
    """Emit one aggregated alert per run summarising its failures.

    - CRITICAL when a majority of a sufficiently-large run failed (pings owners via the
      Discord handler), enriched with the per-reference-code breakdown.
    - else ERROR when there were any permanent failures (surfaced without paging).
    - else nothing (transient blips that the retries absorbed).
    """
    total = view.total
    failed = view.failed
    if not failed:
        return

    summary = _failure_summary(view)
    majority_fail = (
        total >= int(cfg.mirror_failure_min_sample)
        and failed / total >= cfg.mirror_failure_ratio_threshold
    )

    if majority_fail:
        health_logger.critical(
            "Majority of mirrors failing: %d/%d %s targets failed for source %s — %s",
            failed,
            total,
            view.op.name.lower(),
            view.src_ch_id,
            summary,
        )
    elif view.has_permanent:
        health_logger.error(
            "%d/%d %s mirror targets failed (permanent) for source %s — %s",
            failed,
            total,
            view.op.name.lower(),
            view.src_ch_id,
            summary,
        )


async def _run_end_hook(view: RunView) -> None:
    """Fire once per run on completion (from the worker): failure-ratio alert, the
    aggregated not-confirmed-dead warning, the auto-disable sweep (SEND/UPDATE only)
    with count-escalated alert, and the run-summary log line. Sets ``disabled_count`` so
    the final progress render can show it.

    A superseded run is a handover — its successor run does the reporting, so we skip.
    """
    if view.superseded_by_edit:
        return

    flag_mirror_failure_ratio(view)

    not_confirmed = view.not_confirmed_dead
    if not_confirmed:
        health_logger.warning(
            "%d mirror dest(s) of src %s failed permanently but are not confirmed dead "
            "— not counting toward auto-disable: %s",
            len(not_confirmed),
            view.src_ch_id,
            ", ".join(
                f"{dest} (ref {failure.reference_code})"
                for dest, failure in not_confirmed.items()
            ),
        )

    if cfg.disable_bad_channels and view.op in (
        MirrorOperationType.SEND,
        MirrorOperationType.UPDATE,
    ):
        try:
            disabled = await MirroredChannel.disable_failing_mirrors()
        except Exception:
            logging.exception("Auto-disable sweep failed")
            disabled = []
        view.disabled_count = len(disabled)
        if disabled:
            num_disabled = len(disabled)
            message = (
                f"Disabled {num_disabled} mirror(s) (auto-disable sweep): "
                + ", ".join(f"{src}: {dest}" for src, dest in disabled)
            )
            if num_disabled > _DISABLE_CRITICAL_MIN:
                health_logger.critical(message)
            elif num_disabled > _DISABLE_ERROR_MIN:
                health_logger.error(message)
            else:
                health_logger.warning(message)

    elapsed = perf_counter() - view.start_time
    logging.info(
        "Mirror %s for source %s done in %s — %d ok, %d failed, %d cancelled / %d "
        "targets (%.1f ch/s)",
        view.op.name.lower(),
        view.src_ch_id,
        format_duration(elapsed),
        view.delivered,
        view.failed,
        view.cancelled_count,
        view.total,
        view.throughput_resolved / elapsed if elapsed else 0.0,
    )


def _make_recovery_starter(
    bot: CachedFetchBot, client: lb.Client | None
) -> collections.abc.Callable[[RunView], collections.abc.Awaitable[None]]:
    """Progress-card starter passed to the worker for post-restart backlog recovery."""

    async def starter(view: RunView) -> None:
        enable = view.op in (MirrorOperationType.SEND, MirrorOperationType.UPDATE)
        await start_progress_logger(
            bot,
            view,
            title="Mirror recovery progress",
            enable_cancellation=enable,
            client=client,
        )

    return starter


@loader.listener(h.StartedEvent)
async def _start_mirror_worker(
    _event: h.StartedEvent, client: lb.Client = lb.di.INJECTED
) -> None:
    global _client
    _client = client
    bot = t.cast(CachedFetchBot, _event.app)
    await mirror_worker.start(
        bot,
        progress_starter=_make_recovery_starter(bot, client),
        run_end_hook=_run_end_hook,
    )


def ignore_non_src_channels(func: collections.abc.Callable[..., t.Any]):
    async def wrapped_func(event: h.MessageEvent):
        msg = None
        if isinstance(event, (h.MessageCreateEvent, h.MessageUpdateEvent)):
            msg = event.message
        elif isinstance(event, h.MessageDeleteEvent):
            msg = event.old_message

        if msg is None:
            return

        in_src_channel = (
            int(msg.channel_id) in await MirroredChannel.get_or_fetch_all_srcs()
        )
        # In a test env, also process messages that live in one of the test guild(s),
        # so the live test bot can mirror arbitrary channels there. Scoped to
        # msg.guild_id so the bot's presence in *other* servers never drags their
        # channels into the mirror path. guild_id is None in DMs, never a test guild.
        in_test_guild = msg.guild_id is not None and msg.guild_id in cfg.test_env

        if in_src_channel or in_test_guild:
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
            # A permanent error (missing access/perms, unknown channel/message) will
            # never succeed on retry, so retrying would re-log a full traceback roughly
            # every 30s forever. Skip this source's crosspost wait instead of flooding
            # the alerts channel; any genuinely actionable failure surfaces downstream
            # through the mirror send/edit's own classified path.
            if classify_error(e) is ErrorClass.PERMANENT:
                logging.warning(
                    "Skipping crosspost wait for message %s in channel %s: source not "
                    "fetchable (%s).",
                    msg.id,
                    str(followable_name(id=channel.id)),
                    type(e).__name__,
                )
                return
            await discord_error_logger(e, operation="Mirror crosspost")
            await aio.sleep(backoff_timer)
            backoff_timer = min(backoff_timer * 2, 600)
        else:
            break


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
    # Wait for message to be crossposted before mirroring if requested.
    await handle_waiting_for_crosspost(
        msg=msg,
        bot=bot,
        channel=channel,
        wait_for_crosspost=wait_for_crosspost,
    )

    # Enqueue a fresh send fan-out. INSERT-IGNORE makes a duplicate gateway event or a
    # manual re-mirror of an already-enqueued message a no-op (returns 0).
    inserted = await MirrorDelivery.enqueue_send(channel.id, msg.id)
    if not inserted:
        return

    view = RunView(
        op=MirrorOperationType.SEND,
        src_ch_id=channel.id,
        src_msg_id=msg.id,
        total=inserted,
        start_time=perf_counter(),
    )
    mirror_worker.register_view(view)

    # Refetch so the progress card's summary reflects any edit near the crosspost.
    with contextlib.suppress(Exception):
        msg = await bot.rest.fetch_message(msg.channel_id, msg.id)

    await start_progress_logger(
        bot,
        view,
        source_message=msg,
        title="Mirror send progress",
        enable_cancellation=True,
        client=client,
    )
    mirror_worker.nudge()


def is_content_edit(message: h.PartialMessage) -> bool:
    """Whether a MessageUpdateEvent reflects a genuine *content* edit.

    Discord deceptively fires a MessageUpdateEvent for things that are not content
    edits at all: publishing/crossposting an announcement, embed unfurls, flag
    changes, etc. Only a real content edit sets ``edited_timestamp``; the others
    leave it ``None`` or ``UNDEFINED`` (both falsy).
    """
    return bool(message.edited_timestamp)


@loader.listener(h.MessageUpdateEvent)
@ignore_non_src_channels
@utils.ignore_own_user
async def message_update_repeater(
    event: h.MessageUpdateEvent, client: lb.Client = lb.di.INJECTED
):
    # Skip non-content updates (publishes/crossposts, embed unfurls, flag changes). The
    # ledger reconcile edits existing dests AND fresh-sends to any added since, so a
    # publish update arriving before the create handler enqueued would have nothing to
    # bump. Gating on a real edit keeps reconcile-on-edit convergence while making
    # publishes a no-op here. The manual /mirror_update command bypasses this.
    if not is_content_edit(event.message):
        return

    # Ignore edits to messages that haven't been crossposted (published) yet — V2
    # behaviour. Until publish, the create handler is still waiting to mirror the
    # message, so an edit has nothing to update and acting now would race that pending
    # send.
    flags = event.message.flags
    if not isinstance(flags, h.MessageFlag) or h.MessageFlag.CROSSPOSTED not in flags:
        return

    await message_update_repeater_impl(
        t.cast(h.Message, event.message),
        t.cast(CachedFetchBot, event.app),
        client=client,
    )


async def message_update_repeater_impl(
    msg: h.Message, bot: CachedFetchBot, client: lb.Client | None = None
):
    # Reconcile the edit: bump every non-deleted row back to PENDING at the new version
    # and insert rows for any dests added since the send. One transaction, no locks.
    bumped, inserted = await MirrorDelivery.bump_for_edit(msg.channel_id, msg.id)
    total = bumped + inserted
    if total == 0:
        # Not a mirrored message.
        return

    # Supersede any live view for this source (its progress card renders "Superseded by
    # edit" and finalizes); the fresh view takes over the same src_msg_id key.
    old_view = mirror_worker.get_view(msg.id)
    if old_view is not None and not old_view.finalized:
        old_view.superseded_by_edit = True
        mirror_worker.maybe_finalize(old_view)

    view = RunView(
        op=MirrorOperationType.UPDATE,
        src_ch_id=msg.channel_id,
        src_msg_id=msg.id,
        total=total,
        start_time=perf_counter(),
    )
    mirror_worker.register_view(view)

    with contextlib.suppress(Exception):
        msg = await bot.rest.fetch_message(msg.channel_id, msg.id)

    await start_progress_logger(
        bot,
        view,
        source_message=msg,
        title="Mirror update progress",
        enable_cancellation=True,
        client=client,
    )
    mirror_worker.nudge()


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
    # Flag every row for this source as delete-intent. Never-delivered rows go straight
    # to CANCELLED; delivered rows go to PENDING for the worker to delete Discord-side.
    deletion_work = await MirrorDelivery.mark_deleted(msg_id)
    if not deletion_work:
        # Not mirrored, or nothing was ever delivered → nothing to delete.
        return

    view = RunView(
        op=MirrorOperationType.DELETE,
        src_ch_id=None,
        src_msg_id=msg_id,
        total=deletion_work,
        start_time=perf_counter(),
    )
    mirror_worker.register_view(view)

    await start_progress_logger(
        bot,
        view,
        source_message=msg,
        title="Mirror delete progress",
        enable_cancellation=False,
    )
    mirror_worker.nudge()


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
        await MirrorDelivery.prune()
    except Exception as e:
        e.add_note("Exception during routine pruning of the mirror delivery ledger")
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
        view = mirror_worker.get_view(message.id)
        if view is not None and view.op is MirrorOperationType.DELETE:
            await ctx.respond(
                "Can only cancel mirror sends and updates; this message has a delete "
                "in progress.",
                ephemeral=True,
            )
            return

        cancelled = await MirrorDelivery.cancel_pending(message.id)
        if not cancelled and view is None:
            await ctx.respond(
                "This message does not have any operations in progress.", ephemeral=True
            )
            return

        if view is not None:
            view.cancel_requested = True
            for dest in cancelled:
                view.on_cancelled(dest)
            mirror_worker.maybe_finalize(view)
        mirror_worker.nudge()
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
