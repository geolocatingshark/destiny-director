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

"""Mirror subsystem gateway surface: thin enqueue handlers, the progress UI, admin
commands, and the reachability/auto-disable sweep, layered over the ``mirror_delivery``
ledger.

The Discord fan-out itself lives in :mod:`dd.beacon.mirror_worker`. This module's
listeners do one transactional enqueue each (send/edit/delete), start a Components V2
progress card that re-renders from a cheap ledger count until the run drains, and nudge
the worker. A separate low-load task sweeps destination reachability + send perms and
disables mirrors that stay unreachable past a grace window — the delivery hot path does
no perm-probing.
"""

import asyncio as aio
import collections.abc
import contextlib
import logging
import math
import typing as t
from random import randint
from time import perf_counter

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
from ..mirror_core import MirrorOperationType, RunCounts, RunView
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

# Bounded transient-retry budget for a gateway handler's single ledger write.
_HANDLER_DB_MAX_TRIES = 5


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
# Best-effort card send: cap the attempts and give up rather than loop.
_PROGRESS_LOGGER_MAX_TRIES = 5
# Consecutive failed card *edits* after which the update loop gives up (e.g. the card
# was deleted, or perms lost) — so it can't log a traceback every ~60s forever.
_PROGRESS_UPDATE_MAX_FAILS = 5
# Hard cap on a card's lifetime, so a run that somehow never drains can't leave a task
# polling forever.
_CARD_MAX_LIFETIME = 7 * 60 * 60

# A grouped failure line: (reference_code, error_class_name, count, sample_message).
_Breakdown = list[tuple[str, str, int, str]]


def _status_footer(view: RunView, *, final: bool) -> str:
    if not final:
        return "⏳ In progress"
    counts = view.counts
    if counts.delivered == 0 and counts.cancelled > 0:
        base = "❌ Cancelled"
    else:
        base = "✅ Completed"
    return base + (" with errors" if counts.failed else "")


def _progress_bar(view: RunView, width: int = 12) -> str:
    """A single stacked bar of the run's state (done / failed / left).

    Segment widths are proportional to ``total``; blanks absorb rounding, still-pending
    and cancelled targets so the bar never overflows. The trailing percentage is the
    *resolved* fraction (delivered + failed + cancelled).
    """
    counts = view.counts
    total = counts.total or 1
    done = counts.delivered
    fail = counts.failed

    seg_done = round(done / total * width)
    seg_fail = min(round(fail / total * width), width - seg_done)
    seg_blank = width - seg_done - seg_fail

    bar = "🟩" * seg_done + "🟥" * seg_fail + "⬜" * seg_blank
    pct = round(counts.resolved / total * 100)
    return f"{bar}  {pct}%"


def _throughput_line(view: RunView, elapsed_secs: float) -> str | None:
    """Rate + ETA derived from resolved targets over elapsed time.

    Returns ``None`` until at least one target has resolved (the rate is meaningless
    before then) so early renders stay uncluttered. Once every target is resolved the
    ETA is dropped and only the throughput remains.
    """
    resolved = view.counts.throughput_resolved
    if elapsed_secs <= 0 or resolved == 0:
        return None
    rate = resolved / elapsed_secs
    remaining = view.counts.total - view.counts.resolved
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
    breakdown: _Breakdown | None = None,
) -> list[h.api.ComponentBuilder]:
    """Render the mirror progress as a Components V2 container.

    Re-rendered from scratch on each update off the run's latest :class:`RunCounts`. The
    cancel action row is included only when ``enable_cancellation`` and work is still in
    progress; its ``custom_id`` is namespaced by ``src_msg_id`` so two concurrent
    progress messages never cross-fire.
    """
    counts = view.counts
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

    remaining = max(0, counts.total - counts.resolved)
    sections = [
        f"## {title}",
        f"**Source message:** {source_message_field}\n"
        f"**Source channel:** {source_channel_field}",
        # Wrapped in a code block so Discord renders it monospace: the bar cells line
        # up and the colour squares stay distinct instead of being squashed together.
        "```\n"
        f"{_progress_bar(view)}\n"
        f"🟩 {'Completed':<9} {counts.delivered}\n"
        f"🟥 {'Failed':<9} {counts.failed}\n"
        f"⬜ {'Remaining':<9} {remaining}\n"
        + (f"🚫 {'Cancelled':<9} {counts.cancelled}\n" if counts.cancelled else "")
        + "```",
        f"Time taken: {elapsed}",
    ]

    throughput = _throughput_line(view, elapsed_secs)
    if throughput:
        sections[-1] += "\n" + throughput

    if breakdown:
        lines = [
            f"`{ref}` ×{count} ({err_class.lower()})"
            for ref, err_class, count, _sample in breakdown[:_PROGRESS_MAX_BREAKDOWN]
        ]
        if len(breakdown) > _PROGRESS_MAX_BREAKDOWN:
            lines.append(f"…and {len(breakdown) - _PROGRESS_MAX_BREAKDOWN} more")
        sections.append("**Failure breakdown**\n" + "\n".join(lines))

    sections.append(_status_footer(view, final=final))

    container = build_container(
        sections,
        accent_color=cfg.embed_error_color
        if counts.failed
        else cfg.embed_default_color,
    )

    if enable_cancellation and not final:
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
    """Cancel a run's not-yet-delivered destinations and nudge the worker.

    The single cancel path shared by the progress-card button and the ``/mirror_cancel``
    command: cancel PENDING rows in the ledger (the card's next count refresh reflects
    them) and wake the worker so it stops picking them.
    """
    await MirrorDelivery.cancel_pending(view.src_msg_id)
    mirror_worker.nudge()


# The wrapped message listeners (@ignore_non_src_channels / @utils.ignore_own_user)
# strip lightbulb's DI, so a ``client: lb.Client = lb.di.INJECTED`` param on them never
# resolves. Capture the real client once at startup via an unwrapped listener so the
# auto-mirror progress logger can still attach the cancel menu.
_client: lb.Client | None = None

# Strong references to the live card tasks (one per source message). Each task is the
# whole card lifecycle — source resolution, first send and the update loop (see
# _run_card). ``asyncio`` only holds a weak reference to a bare ``create_task`` result,
# so without this a task could be garbage-collected mid-run. A new event for a source
# atomically replaces (and cancels) the old, so only one is ever live per source.
_cards: dict[int, aio.Task[None]] = {}

# Strong reference to the one-shot post-restart backlog-recovery task (same weak-ref
# hazard as ``_cards``); kept alive for the task's lifetime.
_backlog_recovery_task: aio.Task[None] | None = None


def _build_cancel_menu(
    view: RunView,
    client: lb.Client,
    render: collections.abc.Callable[..., list[h.api.ComponentBuilder]],
) -> tuple[lbc.Menu, lbc.MenuHandle]:
    """Build + attach a background lightbulb Menu that routes the cancel button."""
    menu = lbc.Menu()

    async def on_cancel(mctx: lbc.MenuContext) -> None:
        bot = t.cast(CachedFetchBot, mctx.client.app)
        if mctx.user.id not in await bot.fetch_owner_ids():
            await mctx.respond(
                "You are not allowed to cancel this mirror.", ephemeral=True
            )
            return
        await _cancel_run(view)
        # Re-render immediately (the button drops out now the run is cancelling) to
        # acknowledge the interaction; the card loop keeps updating from the ledger.
        await mctx.respond(
            edit=True,
            flags=h.MessageFlag.IS_COMPONENTS_V2,
            components=render(final=False),
        )

    menu.add_interactive_button(
        h.ButtonStyle.DANGER,
        on_cancel,
        custom_id=_cancel_custom_id(view.src_msg_id),
        label="Cancel Mirror",
    )
    handle = menu.attach_persistent(client, timeout=_CANCEL_MENU_TIMEOUT)
    return menu, handle


async def _resolve_source_fields(
    bot: CachedFetchBot,
    source_message: h.Message | None,
    source_channel: h.GuildChannel | None,
    src_ch_id: int | None = None,
) -> tuple[str, str, str, str]:
    """Resolve the card's source message/channel display links (best-effort).

    Any lookup failure degrades to "Unknown"/empty rather than aborting the card — the
    only thing worth retrying is the actual card send, and resolving these outside that
    retry loop is what lets the cancel menu be attached exactly once (not per attempt).
    ``src_ch_id`` lets a recovery/delete card (which carries no cached message/channel)
    still name its source channel from the run's known id.
    """
    source_message_link = ""
    source_channel_link = ""
    source_channel_name = "Unknown"
    source_message_summary = (
        _get_message_summary(source_message) if source_message else "Unknown"
    )
    with contextlib.suppress(Exception):
        if not source_channel and source_message:
            channel_from_message = await bot.fetch_channel(source_message.channel_id)
            if isinstance(channel_from_message, h.GuildChannel):
                source_channel = channel_from_message
        if not source_channel and src_ch_id is not None:
            channel_from_id = await bot.fetch_channel(src_ch_id)
            if isinstance(channel_from_id, h.GuildChannel):
                source_channel = channel_from_id
        if source_channel and source_message:
            source_guild = await bot.fetch_guild(source_channel.guild_id)
            source_message_link = source_message.make_link(source_guild)
        if source_channel:
            source_channel_link = (
                f"https://discord.com/channels/{source_channel.guild_id}/"
                f"{source_channel.id}"
            )
            source_channel_name = source_channel.name or "Unknown"
    return (
        source_message_link,
        source_message_summary,
        source_channel_link,
        source_channel_name,
    )


async def start_progress_card(
    bot: CachedFetchBot,
    view: RunView,
    *,
    source_message: h.Message | None = None,
    source_channel: h.GuildChannel | None = None,
    title: str = "Mirror progress",
    enable_cancellation: bool = False,
    client: lb.Client | None = None,
) -> None:
    """Spawn the progress-card task, superseding any live card for this source.

    One live card per source message. The supersede is atomic: this pops and cancels any
    prior card and registers the new task with **no ``await`` in between**, so two
    near-simultaneous starts for the same source can never both survive (the loser froze
    the older card, then this one takes over). All the async work — resolving source
    links, attaching the cancel menu, the bounded first-send retry and the update loop —
    happens inside the spawned task (:func:`_run_card`), so this returns after
    *scheduling* and the card posts from the task. Registering the task up front also
    makes the whole card lifecycle — including the first-send retry — supersedable.
    """
    old = _cards.pop(view.src_msg_id, None)
    if old is not None:
        old.cancel()
    task = aio.create_task(
        _run_card(
            bot,
            view,
            source_message=source_message,
            source_channel=source_channel,
            title=title,
            enable_cancellation=enable_cancellation,
            client=client,
        )
    )
    _cards[view.src_msg_id] = task
    task.add_done_callback(
        lambda done, sid=view.src_msg_id: (
            _cards.pop(sid, None) if _cards.get(sid) is done else None
        )
    )


async def _run_card(
    bot: CachedFetchBot,
    view: RunView,
    *,
    source_message: h.Message | None,
    source_channel: h.GuildChannel | None,
    title: str,
    enable_cancellation: bool,
    client: lb.Client | None,
) -> None:
    """Resolve source fields, post the first card (bounded retry), run the update loop.

    Runs as the task registered in ``_cards``; a supersede cancels it. Because the
    task's result is never awaited, every exception is contained here — cancellation
    propagates, anything else is logged — and the cancel menu is released on exit.
    """
    menu_handle: lbc.MenuHandle | None = None
    try:
        scl_fields = await _resolve_source_fields(
            bot, source_message, source_channel, view.src_ch_id
        )
        source_message_link, source_message_summary, source_channel_link, scn = (
            scl_fields
        )

        def render(
            *,
            final: bool,
            breakdown: _Breakdown | None = None,
            _scl=source_channel_link,
            _scn=scn,
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
                breakdown=breakdown,
            )

        cancel_client = client if isinstance(client, lb.Client) else _client
        if enable_cancellation and cancel_client is not None:
            _menu, menu_handle = _build_cancel_menu(view, cancel_client, render)

        log_message: h.Message | None = None
        for attempt in range(1, _PROGRESS_LOGGER_MAX_TRIES + 1):
            try:
                log_channel = await bot.fetch_channel(cfg.log_channel)
                if not isinstance(log_channel, h.TextableGuildChannel):
                    raise ValueError("Log channel must be a TextableGuildChannel")
                log_message = await log_channel.send(
                    components=render(final=False),
                    flags=h.MessageFlag.IS_COMPONENTS_V2,
                )
                break
            except aio.CancelledError:
                raise
            except Exception as e:
                e.add_note("Failed to log mirror progress due to exception\n")
                logging.exception(e)
                if attempt >= _PROGRESS_LOGGER_MAX_TRIES:
                    logging.error(
                        "Giving up on the mirror progress logger after %d attempts; "
                        "the mirror itself will still run.",
                        attempt,
                    )
                    return
                await aio.sleep(min(60, 5 * (attempt + 1)))

        assert log_message is not None
        await _card_loop(log_message, view, render, menu_handle)
    except aio.CancelledError:
        raise
    except Exception:
        logging.exception("mirror progress card for source %s failed", view.src_msg_id)
    finally:
        if menu_handle is not None:
            menu_handle.stop_interacting()


async def _card_loop(
    log_message: h.Message,
    view: RunView,
    render: collections.abc.Callable[..., list[h.api.ComponentBuilder]],
    menu_handle: "lbc.MenuHandle | None",
) -> None:
    """Re-render the progress card every few seconds until the run drains.

    Progress is read straight from the ledger (``state_counts``) — the single source of
    truth — so there is no accounting to drift. The run is complete once it has
    rows and none are still PENDING; a hard lifetime cap stops a stuck run from polling
    forever. On completion the run-summary line is logged.
    """
    fails = 0
    started = perf_counter()
    try:
        while True:
            with contextlib.suppress(Exception):
                view.counts = RunCounts.from_state_counts(
                    await MirrorDelivery.state_counts(view.src_msg_id)
                )
            complete = view.counts.total > 0 and view.counts.is_complete
            final = complete or (perf_counter() - started > _CARD_MAX_LIFETIME)

            breakdown: _Breakdown = []
            if view.counts.failed:
                with contextlib.suppress(Exception):
                    breakdown = await MirrorDelivery.failure_breakdown(view.src_msg_id)

            try:
                await log_message.edit(
                    components=render(final=final, breakdown=breakdown),
                    flags=h.MessageFlag.IS_COMPONENTS_V2,
                )
            except Exception as e:
                fails += 1
                if fails >= _PROGRESS_UPDATE_MAX_FAILS:
                    logging.error(
                        "Giving up on the mirror progress card for source %s after %d "
                        "failed updates; the mirror itself is unaffected.",
                        view.src_msg_id,
                        fails,
                    )
                    break
                e.add_note("Failed to log mirror progress due to exception\n")
                logging.exception(e)
                await aio.sleep(min(60, 5 * fails))
                continue

            if final:
                if complete:
                    _log_run_summary(view)
                break
            fails = 0
            await aio.sleep(_PROGRESS_UPDATE_INTERVAL)
    finally:
        if menu_handle is not None:
            menu_handle.stop_interacting()


# Logger whose records surface to the Discord alerts channel (ERROR/CRITICAL) via the
# root DiscordLogHandler, used for mirror-health escalations.
health_logger = logging.getLogger("dd.beacon.mirror.health")

# Escalate a reachability-sweep disable by its blast radius (count of mirrors disabled):
# critical past the first threshold, error past the second, else just a console warning.
_DISABLE_CRITICAL_MIN = 10
_DISABLE_ERROR_MIN = 5

# Escalate a completed run's failures by blast radius so a broad outage pages the owner
# (health_logger only reaches the Discord alerts channel at ERROR/CRITICAL). Any failed
# target is an ERROR; it becomes CRITICAL once the failure count reaches whichever is
# LARGER of a flat floor or a share of the run's targets — so a handful of failures in a
# huge fan-out, or a large fraction of a mid-size one, pages, while a stray failure only
# errors.
_RUN_FAIL_CRITICAL_MIN = 10
_RUN_FAIL_CRITICAL_RATIO = 0.10


def _log_run_summary(view: RunView) -> None:
    """Log a one-line summary of a completed run; escalate to the alerts channel (ERROR,
    or CRITICAL past the blast-radius threshold) if any target failed."""
    counts = view.counts
    elapsed = perf_counter() - view.start_time
    logging.info(
        "Mirror %s for source %s done in %s — %d ok, %d failed, %d cancelled / %d "
        "targets (%.1f ch/s)",
        view.op.name.lower(),
        view.src_ch_id,
        format_duration(elapsed),
        counts.delivered,
        counts.failed,
        counts.cancelled,
        counts.total,
        counts.throughput_resolved / elapsed if elapsed else 0.0,
    )
    if counts.failed:
        critical_at = max(
            _RUN_FAIL_CRITICAL_MIN,
            math.ceil(_RUN_FAIL_CRITICAL_RATIO * counts.total),
        )
        level = logging.CRITICAL if counts.failed >= critical_at else logging.ERROR
        health_logger.log(
            level,
            "Mirror %s for source %s finished with %d/%d target(s) failed.",
            view.op.name.lower(),
            view.src_ch_id,
            counts.failed,
            counts.total,
        )


@loader.task(
    lb.uniformtrigger(hours=cfg.mirror_reachability_sweep_hours, wait_first=True),
    max_failures=-1,
)
async def reachability_sweep(bot: CachedFetchBot = lb.di.INJECTED):
    """Probe every enabled legacy destination for reachability + send perms and disable
    the ones unreachable past the grace window.

    A low-load background job (not the hot path): the perm check is cache-first,
    and a pair is disabled only after it has stayed confirmed-unreachable for
    ``cfg.mirror_unreachable_grace_hours``, so a transient blip never disables a mirror.
    """
    if not cfg.disable_bad_channels:
        return
    await aio.sleep(randint(30, 300))

    try:
        candidates = await MirroredChannel.fetch_reachability_candidates()
    except Exception as e:
        e.add_note("Fetching mirror reachability candidates failed")
        await discord_error_logger(e, operation="Mirror reachability sweep")
        return
    if not candidates:
        return

    sem = aio.Semaphore(8)

    async def probe(
        pair: tuple[int, int],
    ) -> tuple[tuple[int, int], utils.DestVerdict]:
        async with sem:
            try:
                verdict = await utils.confirm_dest_unsendable(bot, pair[1])
            except Exception:
                verdict = utils.DestVerdict.UNKNOWN
        return pair, verdict

    results = await aio.gather(*(probe(pair) for pair in candidates))
    reachable = [pair for pair, v in results if v is utils.DestVerdict.SENDABLE]
    unreachable = [
        pair
        for pair, v in results
        if v
        in (utils.DestVerdict.CONFIRMED_UNSENDABLE, utils.DestVerdict.CONFIRMED_GONE)
    ]

    try:
        disabled = await MirroredChannel.apply_reachability_sweep(
            reachable, unreachable
        )
    except Exception as e:
        e.add_note("Applying the mirror reachability sweep failed")
        await discord_error_logger(e, operation="Mirror reachability sweep")
        return

    if disabled:
        num = len(disabled)
        message = (
            f"Disabled {num} unreachable mirror(s) (reachability sweep): "
            + ", ".join(f"{src}: {dest}" for src, dest in disabled)
        )
        if num > _DISABLE_CRITICAL_MIN:
            health_logger.critical(message)
        elif num > _DISABLE_ERROR_MIN:
            health_logger.error(message)
        else:
            health_logger.warning(message)


@loader.listener(h.StartedEvent)
async def _start_mirror_worker(
    _event: h.StartedEvent, client: lb.Client = lb.di.INJECTED
) -> None:
    global _client
    _client = client
    bot = t.cast(CachedFetchBot, _event.app)
    await mirror_worker.start(bot)
    # Post a recovery card for any source with leftover work, so a post-restart backlog
    # is visible — as a background task so a slow card send can't stall startup. Keep a
    # strong reference (asyncio only holds a weak one; see ``_cards``) so the coroutine
    # can't be garbage-collected while it awaits its DB query / card sends.
    global _backlog_recovery_task
    _backlog_recovery_task = aio.create_task(_recover_backlog_cards(bot, client))


async def _recover_backlog_cards(bot: CachedFetchBot, client: lb.Client) -> None:
    """Register a recovery progress card per source message with non-terminal rows."""
    try:
        backlog = await MirrorDelivery.non_terminal_backlog()
    except Exception:
        logging.exception("mirror backlog recovery query failed")
        return
    for src_msg_id, src_ch_id, _count, any_deleted, any_unsent in backlog:
        op = (
            MirrorOperationType.DELETE
            if any_deleted
            else MirrorOperationType.SEND
            if any_unsent
            else MirrorOperationType.UPDATE
        )
        view = RunView(
            op=op,
            src_ch_id=src_ch_id,
            src_msg_id=src_msg_id,
            start_time=perf_counter(),
        )
        enable = op in (MirrorOperationType.SEND, MirrorOperationType.UPDATE)
        try:
            await start_progress_card(
                bot,
                view,
                title="Mirror recovery progress",
                enable_cancellation=enable,
                client=client,
            )
        except Exception:
            logging.exception(
                "failed to start recovery progress card for %s", src_msg_id
            )
    if backlog:
        logging.info(
            "Mirror backlog recovery: %d source message(s) with pending work.",
            len(backlog),
        )


def ignore_non_src_channels(func: collections.abc.Callable[..., t.Any]):
    async def wrapped_func(event: h.MessageEvent):
        if isinstance(event, (h.MessageCreateEvent, h.MessageUpdateEvent)):
            msg = event.message
            if msg is None:
                return
            channel_id, guild_id = msg.channel_id, msg.guild_id
        elif isinstance(event, h.MessageDeleteEvent):
            # A delete carries channel_id/guild_id on the event itself, so an uncached
            # source message (old_message is None) is still propagated — mark_deleted
            # keys on message_id alone and needs no cached body. guild_id lives only on
            # the guild subclass; None (DM) never matches a test guild.
            channel_id = event.channel_id
            guild_id = getattr(event, "guild_id", None)
        else:
            return

        in_src_channel = (
            int(channel_id) in await MirroredChannel.get_or_fetch_all_srcs()
        )
        # In a test env, also process messages that live in one of the test guild(s),
        # so the live test bot can mirror arbitrary channels there. Scoped to guild_id
        # so the bot's presence in *other* servers never drags their channels into the
        # mirror path. guild_id is None in DMs, never a test guild.
        in_test_guild = guild_id is not None and guild_id in cfg.test_env

        if in_src_channel or in_test_guild:
            return await func(event)

    return wrapped_func


# Total time to wait for a source message to be published (crossposted) before giving up
# and mirroring nothing. One ceiling governs BOTH the crosspost wait_for AND the
# transient fetch-retry loop below — so the whole function can never run longer than
# this, however the time splits between waiting for the publish and retrying a flaky
# source fetch.
_CROSSPOST_WAIT_CEILING_SECONDS = 12 * 60 * 60


async def handle_waiting_for_crosspost(
    msg: h.Message,
    bot: CachedFetchBot,
    channel: h.TextableChannel,
    wait_for_crosspost: bool,
):
    deadline = perf_counter() + _CROSSPOST_WAIT_CEILING_SECONDS
    backoff_timer = 30
    while True:
        remaining = deadline - perf_counter()
        if remaining <= 0:
            # The 12h ceiling elapsed while retrying a flaky source fetch — give up
            # rather than loop (and re-alert) forever, like the wait_for's own cap.
            logging.warning(
                "Giving up crosspost wait for message %s in channel %s after %dh.",
                msg.id,
                str(followable_name(id=channel.id)),
                _CROSSPOST_WAIT_CEILING_SECONDS // 3600,
            )
            return
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
                    # Wait for the publish, but only up to the time left on the ceiling.
                    timeout=remaining,
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
            # Back off, but never past the ceiling (the top-of-loop check then returns).
            await aio.sleep(min(backoff_timer, max(0.0, deadline - perf_counter())))
            backoff_timer = min(backoff_timer * 2, 600)
        else:
            break


async def _ledger_write_with_retry[T](
    what: str, op: collections.abc.Callable[[], collections.abc.Awaitable[T]]
) -> T | None:
    """Run a gateway handler's single ledger write with bounded transient retries.

    The thin handlers do one write, so without this a momentary DB blip at event time
    would silently drop that send/edit/delete forever (nothing durable recorded, the
    gateway event gone). Retries transient failures with capped backoff; gives up
    (alerts + returns ``None``) on a permanent error or once the cap is hit.
    """
    backoff = 5
    for attempt in range(1, _HANDLER_DB_MAX_TRIES + 1):
        try:
            return await op()
        except Exception as e:
            last = attempt >= _HANDLER_DB_MAX_TRIES
            if classify_error(e) is ErrorClass.PERMANENT or last:
                e.add_note(f"Mirror {what} ledger write failed ({attempt} attempt(s))")
                await discord_error_logger(e, operation=f"Mirror {what} enqueue")
                return None
            logging.warning(
                "Mirror %s ledger write transient failure (attempt %d): %s",
                what,
                attempt,
                e,
            )
            await aio.sleep(backoff)
            backoff = min(backoff * 2, 600)
    return None


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
    # manual re-mirror of an already-enqueued message a no-op (returns 0). Retried on a
    # transient DB blip so the send isn't silently lost.
    inserted = await _ledger_write_with_retry(
        "send", lambda: MirrorDelivery.enqueue_send(channel.id, msg.id)
    )
    if not inserted:
        return

    view = RunView(
        op=MirrorOperationType.SEND,
        src_ch_id=channel.id,
        src_msg_id=msg.id,
        start_time=perf_counter(),
    )

    # Refetch so the progress card's summary reflects any edit near the crosspost.
    with contextlib.suppress(Exception):
        msg = await bot.rest.fetch_message(msg.channel_id, msg.id)

    await start_progress_card(
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
    # Cheap early-out for the non-content updates Discord also reports as edits (embed
    # unfurls, flag changes) so an unfurl of an already-delivered message doesn't
    # trigger a needless dest re-edit. The publish/crosspost transition is *not* caught
    # here (a message edited before it was published carries a stale edited_timestamp),
    # so the authoritative guard is bump_for_edit's delivered-baseline gate below: a
    # message that has not been delivered anywhere yet is a true no-op, which is exactly
    # the state of a message at the moment it is published.
    if not is_content_edit(event.message):
        return

    # Ignore edits to messages that haven't been crossposted (published) yet: until
    # publish, the create handler is still waiting to mirror the message, so an edit has
    # nothing to update and acting now would race that pending send.
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
    # Reconcile the edit: bump every non-deleted row at the new version (and, once the
    # message has been delivered somewhere, insert rows for any dests added since the
    # send). One transaction, no locks; retried on a transient DB blip so the edit isn't
    # silently lost.
    result = await _ledger_write_with_retry(
        "edit", lambda: MirrorDelivery.bump_for_edit(msg.channel_id, msg.id)
    )
    if result is None:
        return
    _bumped, _inserted, had_delivered_baseline = result
    if not had_delivered_baseline:
        # Either not a mirrored message, or the edit landed before first delivery — the
        # version bump (if any) folds it into the still-pending send, which fetches the
        # source's live content at delivery time. No separate update run/card, and (the
        # publish transition being exactly this pre-delivery state) no phantom card.
        return

    view = RunView(
        op=MirrorOperationType.UPDATE,
        src_ch_id=msg.channel_id,
        src_msg_id=msg.id,
        start_time=perf_counter(),
    )

    with contextlib.suppress(Exception):
        msg = await bot.rest.fetch_message(msg.channel_id, msg.id)

    await start_progress_card(
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
    deletion_work = await _ledger_write_with_retry(
        "delete", lambda: MirrorDelivery.mark_deleted(msg_id)
    )
    if deletion_work is None:
        return

    mirror_worker.nudge()
    if not deletion_work:
        # Not mirrored, or nothing was ever delivered → nothing to delete Discord-side.
        return

    view = RunView(
        op=MirrorOperationType.DELETE,
        src_ch_id=None,
        src_msg_id=msg_id,
        start_time=perf_counter(),
    )
    await start_progress_card(
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
        cancelled = await MirrorDelivery.cancel_pending(message.id)
        if not cancelled:
            await ctx.respond(
                "This message does not have any sends/updates in progress to cancel.",
                ephemeral=True,
            )
            return
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
