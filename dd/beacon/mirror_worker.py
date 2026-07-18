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

"""The mirror convergence worker: a single pick-and-converge loop over the ledger.

One :class:`MirrorWorker` runs per process (there is only ever one process). Each pass
**picks** a batch of due ``mirror_delivery`` rows (biggest-server-first), converges each
destination against the source's *current* content — fetched fresh from Discord at
delivery time, never stored — and **flushes** every outcome back in one transaction
*before* the next pick. That pick→process→flush→repeat ordering is what keeps a row from
ever being handed out twice without any lease or lock: a retry is bounced to a future
``due_at``, a success is DELIVERED, and only a row still genuinely PENDING (never sent)
is re-picked after a crash.

A fresh send to a Discord announcement (news) channel is recorded with a durable
``crosspost_state = PENDING``; a later pick crossposts it (idempotent — "already
crossposted" counts as success) and marks it DONE, so a crash between send and crosspost
never silently drops the publish.

Progress cards and the reachability sweep live in the extension; this worker
is a pure ledger processor and knows nothing about either.
"""

import asyncio as aio
import collections.abc
import contextlib
import datetime as dt
import logging
import typing as t
from collections import defaultdict
from random import randint

import hikari as h

from ..common import cfg
from ..common.bot import CachedFetchBot
from ..common.emoji_store import AppEmojiStore, rewrite_item_emoji_in_message
from ..common.schemas import (
    CrosspostState,
    DeliveryOutcome,
    DeliveryState,
    MirrorDelivery,
    MirroredChannel,
    OutcomeKind,
    PickedRow,
)
from ..common.utils import (
    ErrorClass,
    classify_error,
    identity_for_exc,
    reference_code,
)
from ..hmessage import HMessage
from . import utils
from .mirror_core import rate_limiter

# Records here surface to the Discord alerts channel (shared with the extension's
# health logger — getLogger returns the same singleton by name).
health_logger = logging.getLogger("dd.beacon.mirror.health")

# Longest the loop backs off before retrying a failed pick or flush.
_MAX_BACKOFF = 60
# Attempts a durable crosspost gets before it is given up on (best-effort).
_CROSSPOST_MAX_ATTEMPTS = 3
# How long stop() waits for the in-flight batch to drain (finish its pick→process→flush
# iteration) before force-cancelling. One batch is bounded (<=pick_batch_size
# rate-limited sends + one flush) and the DB is still live at shutdown, so this is
# generous headroom; only a stuck flush (e.g. DB unreachable) hits it, and those rows
# just recover on the next startup.
_DRAIN_TIMEOUT_SECONDS = 20.0

# Per-destination role-mention map (dest channel id -> optional role id to ping).
_RoleMap = collections.abc.Mapping[int, int | None]


# --- Discord delivery primitives ----------------------------------------------------


def _is_cv2(msg: h.Message) -> bool:
    """Whether the source message was sent as a Components V2 message."""
    return h.MessageFlag.IS_COMPONENTS_V2 in (msg.flags or h.MessageFlag.NONE)


def _send_payload(
    hmsg: HMessage,
    ch_id: int,
    role_ping_per_ch_id: collections.abc.Mapping[int, int | None],
) -> dict[str, t.Any]:
    """Build the ``channel.send`` / ``message.edit`` kwargs for one destination.

    The single source of truth for how a mirrored message is shaped, read from the once-
    per-source rewritten ``HMessage`` (see ``_source_for``): this dest's spoilered role
    ping (when set) is appended — into the first CV2 container, or after plain content —
    by ``HMessage.with_appended_text``, and ``HMessage.to_message_kwargs`` selects the
    send shape (CV2 components + flag, or content + attachments + embeds;
    ``from_message`` already dropped a non-CV2 source's components so admin buttons
    never carry over). ``role_mentions=True`` lets the inline role ping actually fire.
    """
    role_ping = int(role_ping_per_ch_id.get(ch_id) or 0)
    dest = hmsg.with_appended_text(f"||<@&{role_ping}>||") if role_ping else hmsg
    return dest.to_message_kwargs(role_mentions=True)


async def _send_one(
    bot: CachedFetchBot,
    hmsg: HMessage,
    ch_id: int,
    role_ping_per_ch_id: collections.abc.Mapping[int, int | None],
) -> tuple[int, bool]:
    """Send ``hmsg`` to channel ``ch_id`` (rate-limited); return ``(new_id, is_news)``.

    ``is_news`` tells the caller a crosspost is warranted — recorded on the ledger as a
    durable ``crosspost_state = PENDING`` and done by a later pick, never inline.
    """
    async with rate_limiter:
        channel = await bot.fetch_channel(ch_id)
    if not isinstance(channel, h.TextableChannel):
        raise ValueError("Channel is not textable")
    payload = _send_payload(hmsg, ch_id, role_ping_per_ch_id)
    async with rate_limiter:
        mirrored_msg = await channel.send(**payload)
    return mirrored_msg.id, isinstance(channel, h.GuildNewsChannel)


async def edit_one(
    bot: CachedFetchBot,
    hmsg: HMessage,
    ch_id: int,
    dest_msg_id: int,
    role_ping_per_ch_id: collections.abc.Mapping[int, int | None],
) -> int:
    """Edit the recorded dest message in ``ch_id`` to match ``hmsg``; return its id.

    Uses the same :func:`_send_payload` shape as the send path, so an edit can never
    render a mirrored message differently from how it was first sent.
    """
    async with rate_limiter:
        dest_msg = await bot.fetch_message(ch_id, dest_msg_id)
    async with rate_limiter:
        await dest_msg.edit(**_send_payload(hmsg, ch_id, role_ping_per_ch_id))
    return dest_msg_id


async def _delete_one(bot: CachedFetchBot, ch_id: int, dest_msg_id: int) -> None:
    """Delete the recorded dest message. A ``NotFoundError`` counts as success (the
    message is already gone)."""
    try:
        async with rate_limiter:
            dest_msg = await bot.fetch_message(ch_id, dest_msg_id)
        async with rate_limiter:
            await dest_msg.delete()
    except h.NotFoundError:
        return


async def _crosspost_one(bot: CachedFetchBot, ch_id: int, msg_id: int) -> None:
    """Crosspost a mirrored announcement once (rate-limited).

    "Already crossposted" counts as success (idempotent). Any other error raises, so the
    caller can record a durable retry/give-up on the ledger — the backoff lives there,
    not in an in-process sleep.
    """
    try:
        async with rate_limiter:
            await bot.rest.crosspost_message(ch_id, msg_id)
    except h.BadRequestError as e:
        if "This message has already been crossposted" in (e.message or ""):
            return
        raise


# --- The worker ---------------------------------------------------------------------


class MirrorWorker:
    """The single per-process pick → converge → flush loop over ``mirror_delivery``."""

    def __init__(self) -> None:
        self._bot: CachedFetchBot | None = None
        self._store: AppEmojiStore | None = None
        self._running = False
        self._wake: aio.Event | None = None
        self._main_task: aio.Task[None] | None = None
        # Per-src_msg_id cache of the item-emoji-rewritten source HMessage + role map,
        # so a fan-out over many pick batches fetches + rewrites each source once.
        # Cleared whenever the loop goes idle, bounding it to one fan-out's sources.
        self._source_cache: dict[int, tuple[int, HMessage, _RoleMap]] = {}

    # -- lifecycle ---------------------------------------------------------

    async def start(
        self, bot: CachedFetchBot, store: AppEmojiStore | None = None
    ) -> None:
        """Wire the worker to ``bot`` + item-emoji ``store`` and spin up the loop."""
        self._bot = bot
        self._store = store
        self._wake = aio.Event()
        self._running = True
        self._main_task = aio.create_task(self._main_loop())

    async def stop(self, *, drain_timeout: float = _DRAIN_TIMEOUT_SECONDS) -> None:
        """Stop the loop, draining the in-flight batch first.

        Clears ``_running`` and wakes the loop, then *awaits* it: the loop finishes its
        current pick→process→flush iteration and exits on its own, so every send already
        made this batch is flushed (its ``dest_msg_id`` recorded) and cannot re-send on
        the next startup. Draining is bounded to one batch — ``_running`` is checked at
        the top of the loop, so no *new* work is picked. Only if the drain exceeds
        ``drain_timeout`` (e.g. the DB is unreachable so the flush cannot complete) is
        the task force-cancelled, leaving its rows PENDING to be re-picked and
        re-converged idempotently on restart (the accepted crash-dup window). A no-op
        when never started; idempotent.
        """
        self._running = False
        if self._wake is not None:
            self._wake.set()
        task = self._main_task
        if task is None:
            return
        try:
            # Let the current iteration finish and the loop exit cleanly. wait_for
            # cancels the task itself on timeout, so a stuck flush can't hang shutdown.
            await aio.wait_for(task, timeout=drain_timeout)
        except TimeoutError:
            with contextlib.suppress(aio.CancelledError):
                await task
        finally:
            self._main_task = None

    def nudge(self) -> None:
        """Wake the loop early (a gateway handler just enqueued work)."""
        if self._wake is not None:
            self._wake.set()

    async def outstanding_count(self) -> int:
        """PENDING row count — the controller's pre-restart 'work in progress' gate."""
        return await MirrorDelivery.outstanding_count()

    def _bot_or_raise(self) -> CachedFetchBot:
        if self._bot is None:
            raise RuntimeError("MirrorWorker used before start() wired a bot")
        return self._bot

    # -- loop --------------------------------------------------------------

    async def _main_loop(self) -> None:
        """Pick a due batch, converge it, flush every outcome, repeat.

        A pick failure backs off; a convergence bug is caught and logged (a dead loop
        would silently stop all mirroring). Flush is retried until it succeeds before
        the next pick, so a row's outcome is durable before it could be re-picked.
        """
        assert self._wake is not None
        backoff = 5
        while self._running:
            self._wake.clear()
            try:
                batch = await MirrorDelivery.pick_batch(cfg.mirror_pick_batch_size)
            except Exception:
                logging.exception("mirror pick failed; backing off %ss", backoff)
                await aio.sleep(backoff)
                backoff = min(_MAX_BACKOFF, backoff * 2)
                continue
            backoff = 5
            if batch:
                try:
                    outcomes = await self._process(batch)
                except Exception:
                    logging.exception("mirror convergence pass failed; continuing")
                    outcomes = []
                await self._flush(outcomes)
                await self._evict_resolved_sources(batch)
            else:
                # Idle: the source cache only helps within an active fan-out.
                self._source_cache.clear()
                with contextlib.suppress(TimeoutError):
                    await aio.wait_for(
                        self._wake.wait(), timeout=cfg.mirror_poll_interval
                    )

    async def _process(self, batch: list[PickedRow]) -> list[DeliveryOutcome]:
        """Converge a picked batch: deliveries grouped by source, crossposts per row.

        A single semaphore bounds in-flight Discord calls across the whole batch; the
        global rate limiter bounds request rate.
        """
        sem = aio.Semaphore(cfg.mirror_max_concurrency)
        deliveries: dict[int, list[PickedRow]] = defaultdict(list)
        crossposts: list[PickedRow] = []
        for row in batch:
            if row.state == DeliveryState.PENDING.value:
                deliveries[row.src_msg_id].append(row)
            elif row.crosspost_state == CrosspostState.PENDING.value:
                crossposts.append(row)

        outcomes: list[DeliveryOutcome] = []
        for group in await aio.gather(
            *(self._process_group(sem, rows) for rows in deliveries.values())
        ):
            outcomes.extend(group)
        outcomes.extend(
            await aio.gather(*(self._crosspost_row(sem, r) for r in crossposts))
        )
        return outcomes

    async def _process_group(
        self, sem: aio.Semaphore, rows: list[PickedRow]
    ) -> list[DeliveryOutcome]:
        """Converge all picked delivery rows of one source message.

        Fetches the source content + role map once for the whole group (cached per
        source *version*). Deleted rows need no source, so a source-fetch failure only
        sidelines the non-deleted rows.
        """
        src_msg_id = rows[0].src_msg_id
        src_ch_id = rows[0].src_ch_id
        needs_source = any(not r.deleted for r in rows)
        hmsg: HMessage | None = None
        role_map: _RoleMap = {}
        outcomes: list[DeliveryOutcome] = []
        if needs_source:
            version = rows[0].desired_version
            try:
                hmsg, role_map = await self._source_for(src_ch_id, src_msg_id, version)
            except Exception as e:
                outcomes.extend(self._handle_group_source_failure(rows, e))
                rows = [r for r in rows if r.deleted]  # deleted rows need no source
                if not rows:
                    return outcomes
                hmsg = None
        outcomes.extend(
            await aio.gather(*(self._deliver_row(sem, r, hmsg, role_map) for r in rows))
        )
        return outcomes

    async def _source_for(
        self, src_ch_id: int, src_msg_id: int, version: int
    ) -> tuple[HMessage, _RoleMap]:
        """Fetch, item-emoji-rewrite and cache the source as an :class:`HMessage` + role
        map, keyed by ``(src_msg_id, version)``.

        The rewrite runs once per source version — an edit bumps the version, so edited
        content still converges, but a multi-batch fan-out reuses the one rewrite for
        every destination (the per-dest role ping is added at send time).
        """
        cached = self._source_cache.get(src_msg_id)
        if cached is not None and cached[0] == version:
            return cached[1], cached[2]
        bot = self._bot_or_raise()
        async with rate_limiter:
            msg = await bot.rest.fetch_message(src_ch_id, src_msg_id)
        msg.embeds = utils.filter_discord_autoembeds(msg)
        hmsg = HMessage.from_message(msg)
        if _is_cv2(msg) and not hmsg.components:
            # A CV2 source whose components did not rebuild (from_message swallows the
            # NotImplementedError → empty HMessage, and a CV2 message has no content or
            # embeds either). Sending it would mirror a blank / ping-only message; fail
            # loudly instead so the row retries/terminalises, matching the old
            # rebuild_components-raises path rather than silently losing the post.
            raise ValueError(
                f"CV2 source message {src_msg_id} has no rebuildable components"
            )
        if self._store is not None:
            await rewrite_item_emoji_in_message(self._store, hmsg)
        role_map = await MirroredChannel.fetch_mirror_and_role_mention_id(src_ch_id)
        self._source_cache[src_msg_id] = (version, hmsg, role_map)
        return hmsg, role_map

    async def _evict_resolved_sources(self, batch: list[PickedRow]) -> None:
        """Drop cached source content for any batch source whose fan-out has resolved.

        The per-source content cache is otherwise only cleared when the loop goes idle,
        so under sustained load it would retain a Message per source ever seen. After
        each batch, keep only the sources that still have a PENDING non-deleted delivery
        row (they still need their content on a later pick) and evict the rest. A query
        failure is non-fatal — the cache just isn't trimmed this pass (idle clears it).
        """
        cached = self._source_cache.keys() & {row.src_msg_id for row in batch}
        if not cached:
            return
        try:
            still_needed = await MirrorDelivery.sources_needing_source_content(cached)
        except Exception:
            logging.exception("mirror source-cache eviction query failed; skipping")
            return
        for src_msg_id in cached - still_needed:
            self._source_cache.pop(src_msg_id, None)

    def _handle_group_source_failure(
        self, rows: list[PickedRow], exc: Exception
    ) -> list[DeliveryOutcome]:
        """A once-per-group source fetch failed.

        Permanent (source gone) → cancel the non-deleted rows (nothing to deliver).
        Transient → back them off, honouring the per-op attempt cap so a persistently
        unfetchable source eventually terminalizes instead of looping forever.
        """
        non_deleted = [r for r in rows if not r.deleted]
        if classify_error(exc) is ErrorClass.PERMANENT:
            health_logger.warning(
                "Mirror source message %s unfetchable (%s) — cancelling %d dest(s).",
                rows[0].src_msg_id,
                type(exc).__name__,
                len(non_deleted),
            )
            return [self._outcome(OutcomeKind.CANCELLED, r) for r in non_deleted]

        ref = reference_code(identity_for_exc(exc))
        err_class = classify_error(exc)
        outcomes: list[DeliveryOutcome] = []
        for row in non_deleted:
            attempts = row.attempts + 1
            is_send = row.dest_msg_id is None
            cap = (
                cfg.mirror_send_max_attempts
                if is_send
                else cfg.mirror_edit_max_attempts
            )
            if attempts >= cap:
                outcomes.append(
                    self._outcome(
                        OutcomeKind.TERMINAL,
                        row,
                        attempts=attempts,
                        error_ref=ref,
                        error_class=err_class.name,
                        error_msg=str(exc)[:256],
                    )
                )
            else:
                outcomes.append(self._transient(row, exc, ref, attempts=attempts))
        return outcomes

    async def _deliver_row(
        self,
        sem: aio.Semaphore,
        row: PickedRow,
        hmsg: HMessage | None,
        role_map: _RoleMap,
    ) -> DeliveryOutcome:
        """Converge one destination row; return its outcome (never raises)."""
        async with sem:
            try:
                return await self._do_delivery(row, hmsg, role_map)
            except Exception as e:
                failure = e
        return self._handle_row_failure(row, failure)

    async def _do_delivery(
        self,
        row: PickedRow,
        hmsg: HMessage | None,
        role_map: _RoleMap,
    ) -> DeliveryOutcome:
        """Perform one row's Discord op and return its outcome.

        A fresh send to a news channel is stamped ``crosspost_pending`` so the flusher
        records a durable ``crosspost_state = PENDING`` for a later pick to converge.
        """
        bot = self._bot_or_raise()
        if row.deleted:
            if row.dest_msg_id is None:
                # Never delivered → nothing to delete Discord-side.
                return self._outcome(OutcomeKind.CANCELLED, row)
            await _delete_one(bot, row.dest_ch_id, row.dest_msg_id)
            return self._outcome(OutcomeKind.DELETE_SUCCESS, row)
        assert hmsg is not None
        if row.dest_msg_id is None:
            new_id, is_news = await _send_one(bot, hmsg, row.dest_ch_id, role_map)
            return self._outcome(
                OutcomeKind.SUCCESS, row, dest_msg_id=new_id, crosspost_pending=is_news
            )
        await edit_one(bot, hmsg, row.dest_ch_id, row.dest_msg_id, role_map)
        return self._outcome(OutcomeKind.SUCCESS, row, dest_msg_id=row.dest_msg_id)

    async def _crosspost_row(
        self, sem: aio.Semaphore, row: PickedRow
    ) -> DeliveryOutcome:
        """Durably crosspost one delivered news-channel row; return its outcome.

        Success ("already crossposted") → DONE. A retryable failure below the cap →
        CROSSPOST_RETRY (ledger backoff). A permanent error or the cap → give up (DONE,
        best-effort) so the row does not churn forever.
        """
        bot = self._bot_or_raise()
        assert row.dest_msg_id is not None
        async with sem:
            try:
                await _crosspost_one(bot, row.dest_ch_id, row.dest_msg_id)
            except Exception as e:
                attempts = row.attempts + 1
                if classify_error(e) is ErrorClass.PERMANENT or (
                    attempts >= _CROSSPOST_MAX_ATTEMPTS
                ):
                    health_logger.warning(
                        "Giving up crosspost dest %s msg %s after %d attempt(s): %s",
                        row.dest_ch_id,
                        row.dest_msg_id,
                        attempts,
                        e,
                    )
                    return self._outcome(OutcomeKind.CROSSPOST_DONE, row)
                due = dt.datetime.now(tz=dt.UTC) + dt.timedelta(
                    seconds=randint(cfg.mirror_retry_min, cfg.mirror_retry_max)
                )
                return self._outcome(
                    OutcomeKind.CROSSPOST_RETRY, row, attempts=attempts, due_at=due
                )
        return self._outcome(OutcomeKind.CROSSPOST_DONE, row)

    def _handle_row_failure(self, row: PickedRow, exc: Exception) -> DeliveryOutcome:
        """Classify a delivery exception → a transient backoff or a terminal FAILED.

        Terminal when the error is PERMANENT or the per-op attempt cap is hit. Failure
        classification stays here even though the perm *probe* moved to the reachability
        sweep — the loop must never retry a permanently-dead channel forever.
        """
        ref = reference_code(identity_for_exc(exc))
        err_class = classify_error(exc)
        attempts = row.attempts + 1
        is_send = row.dest_msg_id is None and not row.deleted
        cap = cfg.mirror_send_max_attempts if is_send else cfg.mirror_edit_max_attempts
        if err_class is ErrorClass.PERMANENT or attempts >= cap:
            return self._outcome(
                OutcomeKind.TERMINAL,
                row,
                attempts=attempts,
                error_ref=ref,
                error_class=err_class.name,
                error_msg=str(exc)[:256],
            )
        return self._transient(row, exc, ref, attempts=attempts)

    def _transient(
        self, row: PickedRow, exc: Exception, ref: str, *, attempts: int
    ) -> DeliveryOutcome:
        """Build a TRANSIENT outcome re-scheduled with a randomised ledger backoff."""
        due = dt.datetime.now(tz=dt.UTC) + dt.timedelta(
            seconds=randint(cfg.mirror_retry_min, cfg.mirror_retry_max)
        )
        return self._outcome(
            OutcomeKind.TRANSIENT,
            row,
            attempts=attempts,
            due_at=due,
            error_ref=ref,
            error_class=classify_error(exc).name,
            error_msg=str(exc)[:256],
        )

    @staticmethod
    def _outcome(kind: OutcomeKind, row: PickedRow, **kwargs: t.Any) -> DeliveryOutcome:
        """Build a :class:`DeliveryOutcome` stamped with the picked row's identity + the
        version this attempt delivered (the flusher's guard compares it)."""
        return DeliveryOutcome(
            kind=kind,
            src_msg_id=row.src_msg_id,
            dest_ch_id=row.dest_ch_id,
            version=row.desired_version,
            **kwargs,
        )

    # -- flush -------------------------------------------------------------

    async def _flush(self, outcomes: list[DeliveryOutcome]) -> None:
        """Write a batch of outcomes back, retrying until durable.

        The loop must not pick again until this batch is written — an unwritten outcome
        leaves its row PENDING, and re-picking it would re-send. Retries with a capped
        backoff; a shutdown cancels the task (rows recover on restart).
        """
        if not outcomes:
            return
        backoff = 5
        while True:
            try:
                await MirrorDelivery.flush_outcomes(outcomes)
                return
            except aio.CancelledError:
                raise
            except Exception:
                logging.exception(
                    "mirror flush of %d outcome(s) failed; retrying in %ss",
                    len(outcomes),
                    backoff,
                )
                await aio.sleep(backoff)
                backoff = min(_MAX_BACKOFF, backoff * 2)


# The single per-process worker instance.
mirror_worker = MirrorWorker()
