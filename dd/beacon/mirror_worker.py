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

"""The mirror convergence worker: the durable-ledger replacement for the in-memory
fan-out.

One :class:`MirrorWorker` runs per bot process. Its **claim loop** repeatedly claims a
batch of due ``mirror_delivery`` rows (``FOR UPDATE SKIP LOCKED``, biggest-server-first)
and converges each destination against the source's *current* content — fetched fresh
from Discord at delivery time, never stored. Delivery results are buffered and written
back by a dedicated **flusher** coroutine.

Two invariants shape the split:

    - Delivery coroutines never await the DB; the flusher never awaits Discord. (The
      once-per-group source fetch + role-mention read is the sanctioned exception —
      it runs before the per-row delivery tasks.)
    - A dest message id, once created Discord-side and observed, is *always* recorded —
      even when the version guard sends the row back to PENDING — so re-convergence
      edits instead of re-sending.

Progress is tracked in-memory by :class:`RunView` objects (registered by the gateway
handlers, or synthesised here for post-restart backlog recovery); the worker records
outcomes into them and fires a run-end hook on completion.
"""

import asyncio as aio
import collections.abc
import contextlib
import datetime as dt
import logging
import os
import socket
import typing as t
from collections import defaultdict
from random import randint
from time import perf_counter

import hikari as h

from ..common import cfg
from ..common.bot import CachedFetchBot
from ..common.components import rebuild_components
from ..common.schemas import (
    ClaimedRow,
    DeliveryOutcome,
    MirrorDelivery,
    MirroredChannel,
    OutcomeKind,
)
from ..common.utils import (
    ErrorClass,
    classify_error,
    identity_for_exc,
    reference_code,
)
from . import utils
from .mirror_core import MirrorOperationType, RunFailure, RunView, rate_limiter

# Records here surface to the Discord alerts channel (shared with the extension's
# health logger — getLogger returns the same singleton by name).
health_logger = logging.getLogger("dd.beacon.mirror.health")

# Longest a single failed flush backs off before retrying the write-back.
_FLUSH_MAX_BACKOFF = 60

# Per-destination role-mention map (dest channel id -> optional role id to ping).
_RoleMap = collections.abc.Mapping[int, int | None]

# Callback the gateway handlers / recovery use to spin up a progress card for a view.
ProgressStarter = collections.abc.Callable[["RunView"], collections.abc.Awaitable[None]]
# Callback fired once per run on completion (flag failures, sweep auto-disable, alert).
RunEndHook = collections.abc.Callable[["RunView"], collections.abc.Awaitable[None]]


# --- Discord delivery primitives (moved here verbatim from the old extension) -------


def _is_cv2(msg: h.Message) -> bool:
    """Whether a message was sent as a Components V2 message."""
    return h.MessageFlag.IS_COMPONENTS_V2 in (msg.flags or h.MessageFlag.NONE)


def add_role_ping_to_msg(
    msg_content: str | None,
    dest_channel_id: int,
    role_ping_per_ch_id: collections.abc.Mapping[int, int | None],
) -> str | None:
    """Append this dest's spoilered role ping to the content (no-op when unset)."""
    role_ping = int(role_ping_per_ch_id.get(dest_channel_id) or 0)
    if role_ping:
        msg_content = msg_content.strip("\n") + "\n\n" if msg_content else ""
        msg_content += f"||<@&{role_ping}>||"
    return msg_content


def _cv2_components_for(
    msg: h.Message,
    dest_channel_id: int,
    role_ping_per_ch_id: collections.abc.Mapping[int, int | None],
) -> list[h.api.ComponentBuilder]:
    """Rebuild a CV2 source message's components for re-sending to a destination.

    A CV2 message has no content field, so the dest's spoilered role ping is appended as
    a text display inside the (first) container rather than to message content.
    """
    components = rebuild_components(msg.components)
    role_ping = int(role_ping_per_ch_id.get(dest_channel_id) or 0)
    if role_ping:
        suffix = f"||<@&{role_ping}>||"
        for component in components:
            if isinstance(component, h.impl.ContainerComponentBuilder):
                component.add_text_display(suffix)
                break
        else:
            components.append(h.impl.TextDisplayComponentBuilder(content=suffix))
    return components


def _send_payload(
    msg: h.Message,
    ch_id: int,
    role_ping_per_ch_id: collections.abc.Mapping[int, int | None],
) -> dict[str, t.Any]:
    """Build the ``channel.send`` / ``message.edit`` kwargs for one destination.

    The single source of truth for how a mirrored message is shaped, shared by the send
    and edit paths so they can never drift: a CV2 source is re-sent as rebuilt
    components (the dest role ping appended inside the container); a plain message is
    re-sent as content + attachments + embeds (components are intentionally dropped so
    the main server's admin buttons are not carried to destinations).
    """
    if _is_cv2(msg):
        return {
            "components": _cv2_components_for(msg, ch_id, role_ping_per_ch_id),
            "flags": h.MessageFlag.IS_COMPONENTS_V2,
            "role_mentions": True,
        }
    return {
        "content": add_role_ping_to_msg(msg.content, ch_id, role_ping_per_ch_id),
        "attachments": msg.attachments,
        "embeds": msg.embeds,
        "role_mentions": True,
    }


async def _send_one(
    bot: CachedFetchBot,
    msg: h.Message,
    ch_id: int,
    role_ping_per_ch_id: collections.abc.Mapping[int, int | None],
) -> tuple[int, bool]:
    """Send ``msg`` to channel ``ch_id`` (rate-limited); return ``(new_id, is_news)``.

    ``is_news`` tells the caller a crosspost is warranted — done as non-fatal post-work
    *outside* the delivery concurrency slot (see :meth:`MirrorWorker._deliver_row`), so
    a flaky crosspost's backoff can never pin one of the finite send slots.
    """
    channel = await bot.fetch_channel(ch_id)
    if not isinstance(channel, h.TextableChannel):
        raise ValueError("Channel is not textable")
    async with rate_limiter:
        mirrored_msg = await channel.send(
            **_send_payload(msg, ch_id, role_ping_per_ch_id)
        )
    return mirrored_msg.id, isinstance(channel, h.GuildNewsChannel)


async def _crosspost_one(bot: CachedFetchBot, ch_id: int, msg_id: int) -> None:
    """Crosspost a freshly-mirrored announcement message (non-fatal, capped retries).

    The "already crossposted" 400 counts as success. Runs outside the delivery
    semaphore, so its backoff sleeps never occupy a send slot. A duplicated (rather than
    shared) copy of anchor's ``crosspost_message_with_retries``: this one is
    attempt-capped where anchor's loops unboundedly, and keeping it here avoids a
    beacon→anchor import across the two bots' boundary.
    """
    crosspost_backoff = 30
    for _ in range(3):
        try:
            async with rate_limiter:
                await bot.rest.crosspost_message(ch_id, msg_id)
        except Exception as e:
            if (
                isinstance(e, h.BadRequestError)
                and "This message has already been crossposted" in e.message
            ):
                return
            e.add_note(
                f"Failed to crosspost message in channel {ch_id} due to exception\n"
            )
            logging.warning(e, exc_info=e)
            await aio.sleep(crosspost_backoff)
            crosspost_backoff = crosspost_backoff * 2
        else:
            return


async def edit_one(
    bot: CachedFetchBot,
    msg: h.Message,
    ch_id: int,
    dest_msg_id: int,
    role_ping_per_ch_id: collections.abc.Mapping[int, int | None],
) -> int:
    """Edit the recorded dest message in ``ch_id`` to match ``msg``; return its id.

    Uses the same :func:`_send_payload` shape as the send path, so an edit can never
    render a mirrored message differently from how it was first sent.
    """
    async with rate_limiter:
        dest_msg = await bot.fetch_message(ch_id, dest_msg_id)
    async with rate_limiter:
        await dest_msg.edit(**_send_payload(msg, ch_id, role_ping_per_ch_id))
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


# --- The worker ---------------------------------------------------------------------


class MirrorWorker:
    """One-per-process claim loop + flusher over the ``mirror_delivery`` ledger."""

    def __init__(self) -> None:
        self._bot: CachedFetchBot | None = None
        self.worker_id: str = f"{socket.gethostname()}:{os.getpid()}"[:64]
        self._running = False
        self._wake: aio.Event | None = None
        self._buffer_event: aio.Event | None = None
        self._buffer: list[DeliveryOutcome] = []
        self._main_task: aio.Task[None] | None = None
        self._flusher_task: aio.Task[None] | None = None
        self._recovery_task: aio.Task[None] | None = None
        self._end_tasks: set[aio.Task[None]] = set()
        self.run_views: dict[int, RunView] = {}
        self._progress_starter: ProgressStarter | None = None
        self._run_end_hook: RunEndHook | None = None
        # Per-(src_msg_id, version) cache of the fetched source message + role map, so a
        # fan-out that spans many claim batches fetches each only once per version
        # (an edit bumps the version → cache miss → fresh content). Bounded by evicting
        # a run's entry when it finalizes.
        self._source_cache: dict[int, tuple[int, h.Message, _RoleMap]] = {}

    # -- lifecycle ---------------------------------------------------------

    async def start(
        self,
        bot: CachedFetchBot,
        *,
        progress_starter: ProgressStarter | None = None,
        run_end_hook: RunEndHook | None = None,
    ) -> None:
        """Wire the worker to ``bot`` and spin up the claim + flusher loops.

        Backlog recovery runs as a *background* task, not inline: it awaits a progress
        card per backlog source (each up to ~70s of retry sleeps if the log channel is
        down), and blocking the whole worker's startup on that would stall all mirroring
        for minutes. The claim loop converges the backlog regardless of whether its card
        was posted.
        """
        self._bot = bot
        self._progress_starter = progress_starter
        self._run_end_hook = run_end_hook
        self._wake = aio.Event()
        self._buffer_event = aio.Event()
        self._running = True
        self._main_task = aio.create_task(self._main_loop())
        self._flusher_task = aio.create_task(self._flusher_loop())
        self._recovery_task = aio.create_task(self._recover_backlog())

    async def stop(self) -> None:
        """Stop claiming, then **drain** buffered outcomes before teardown.

        Order matters for the "a dest id once observed is always recorded" invariant:
        cancel the claim loop first (no new outcomes), then flush whatever is still
        buffered so freshly-created ``dest_msg_id``s reach the DB. Skipping the drain
        would leave those rows CLAIMED with a NULL dest id, and after the stale-claim
        window a restart would re-send them (duplicate messages). The drain is bounded
        so a dead DB cannot hang shutdown forever.
        """
        self._running = False
        if self._wake is not None:
            self._wake.set()
        if self._buffer_event is not None:
            self._buffer_event.set()
        # 1) Stop producing outcomes.
        stop_producers = [
            task
            for task in (self._main_task, self._recovery_task, *self._end_tasks)
            if task is not None
        ]
        for task in stop_producers:
            task.cancel()
        if stop_producers:
            await aio.gather(*stop_producers, return_exceptions=True)
        # 2) Stop the flusher loop, then drain the buffer synchronously (bounded).
        if self._flusher_task is not None:
            self._flusher_task.cancel()
            await aio.gather(self._flusher_task, return_exceptions=True)
        await self._drain_buffer()

    async def _drain_buffer(self, max_attempts: int = 5) -> None:
        """Best-effort final flush of buffered outcomes during shutdown (bounded)."""
        for _ in range(max_attempts):
            if not self._buffer:
                return
            if not await self._flush_once():
                await aio.sleep(1)
        if self._buffer:
            logging.error(
                "mirror shutdown: %d outcome(s) could not be flushed; their rows will "
                "be recovered after the stale-claim window",
                len(self._buffer),
            )

    def nudge(self) -> None:
        """Wake the claim loop early (a gateway handler just enqueued work)."""
        if self._wake is not None:
            self._wake.set()

    @property
    def in_progress_count(self) -> int:
        """Number of runs with work still outstanding — used by the controller's
        pre-restart mirror check.

        Counts non-finalized runs, but also reports at least ``1`` while the outcome
        buffer is non-empty: those buffered ``dest_msg_id``s are observed-but-not-yet-
        durable, so restarting now would lose them and re-send. Surfacing them keeps the
        controller's DANGER gate honest.
        """
        pending = sum(1 for view in self.run_views.values() if not view.finalized)
        if not pending and self._buffer:
            return 1
        return pending

    # -- run-view registry -------------------------------------------------

    def register_view(self, view: RunView) -> None:
        """Register (or replace) the progress view for a source message."""
        self.run_views[view.src_msg_id] = view

    def get_view(self, src_msg_id: int) -> RunView | None:
        """The currently-registered view for a source message, if any."""
        return self.run_views.get(src_msg_id)

    def evict(self, view: RunView) -> None:
        """Drop a finalized view — but only if it is still the registered one, so a
        superseding edit that re-registered under the same src_msg_id is untouched."""
        if self.run_views.get(view.src_msg_id) is view:
            del self.run_views[view.src_msg_id]
            self._source_cache.pop(view.src_msg_id, None)

    # -- claim loop --------------------------------------------------------

    async def _main_loop(self) -> None:
        """Claim → converge → reconcile, forever.

        Each pass claims a due batch, converges it, then reconciles *every* active run
        view against the ledger (see :meth:`_reconcile_views`) — reconciling on every
        pass, batch or not, is what finalizes runs whose completion the in-memory view
        can't see on its own: a delete that only cancelled rows, a run whose outcomes
        landed while its view was momentarily unregistered, or a cancel whose last
        in-flight row just drained. A claim failure backs off; any *other* exception is
        caught and logged rather than being allowed to kill the loop silently (a dead
        claim loop stops all mirroring until restart).
        """
        assert self._wake is not None
        backoff = 5
        while self._running:
            self._wake.clear()
            now = dt.datetime.now(tz=dt.UTC)
            stale_cutoff = now - dt.timedelta(seconds=cfg.mirror_claim_stale_seconds)
            try:
                batch = await MirrorDelivery.claim_batch(
                    self.worker_id,
                    cfg.mirror_claim_batch_size,
                    stale_cutoff,
                    now=now,
                )
            except Exception:
                logging.exception("mirror claim failed; backing off %ss", backoff)
                await aio.sleep(backoff)
                backoff = min(_FLUSH_MAX_BACKOFF, backoff * 2)
                continue
            backoff = 5
            try:
                if batch:
                    await self.process(batch)
                await self._reconcile_views()
            except Exception:
                # A bug in convergence/reconcile must not silently kill the claim loop
                # (its exception would sit unretrieved on the strongly-referenced task
                # with no warning). Log and carry on to the next pass.
                logging.exception("mirror convergence pass failed; continuing")
            if not batch:
                with contextlib.suppress(TimeoutError):
                    await aio.wait_for(
                        self._wake.wait(), timeout=cfg.mirror_poll_interval
                    )

    async def process(self, batch: list[ClaimedRow]) -> None:
        """Converge a claimed batch: group by source message, deliver each row.

        A single semaphore bounds in-flight Discord calls across the whole batch; the
        global rate limiter bounds request rate.
        """
        sem = aio.Semaphore(cfg.mirror_max_concurrency)
        groups: dict[int, list[ClaimedRow]] = defaultdict(list)
        for row in batch:
            groups[row.src_msg_id].append(row)
        await aio.gather(*(self._process_group(sem, rows) for rows in groups.values()))

    async def _process_group(self, sem: aio.Semaphore, rows: list[ClaimedRow]) -> None:
        """Converge all claimed rows of one source message.

        Fetches the source content + role map once for the whole group (the sanctioned
        exception to "delivery never awaits the DB"), cached per source *version* so a
        multi-batch fan-out doesn't re-fetch either on every batch. Deleted rows need no
        source, so a source-fetch failure only sidelines the non-deleted rows.
        """
        src_msg_id = rows[0].src_msg_id
        src_ch_id = rows[0].src_ch_id
        view = self.get_view(src_msg_id)

        needs_source = any(not r.deleted for r in rows)
        msg: h.Message | None = None
        role_map: _RoleMap = {}
        if needs_source:
            version = rows[0].desired_version
            try:
                msg, role_map = await self._source_for(src_ch_id, src_msg_id, version)
            except Exception as e:
                await self._handle_group_source_failure(view, rows, e)
                # Deleted rows in the group can still be processed (no source needed).
                rows = [r for r in rows if r.deleted]
                if not rows:
                    return
                msg = None

        await aio.gather(
            *(self._deliver_row(sem, view, row, msg, role_map) for row in rows)
        )

    async def _source_for(
        self, src_ch_id: int, src_msg_id: int, version: int
    ) -> tuple[h.Message, _RoleMap]:
        """Fetch (+ cache per ``(src_msg_id, version)``) the source message + role map.

        Content is fetched fresh once per *version*: an edit bumps the version, so this
        still converges edited content, but the ~60 redundant REST fetches + role-map
        reads a large multi-batch fan-out would otherwise do collapse to one.
        """
        cached = self._source_cache.get(src_msg_id)
        if cached is not None and cached[0] == version:
            return cached[1], cached[2]
        bot = self._bot_or_raise()
        msg = await bot.rest.fetch_message(src_ch_id, src_msg_id)
        msg.embeds = utils.filter_discord_autoembeds(msg)
        role_map = await MirroredChannel.fetch_mirror_and_role_mention_id(src_ch_id)
        self._source_cache[src_msg_id] = (version, msg, role_map)
        return msg, role_map

    async def _handle_group_source_failure(
        self,
        view: RunView | None,
        rows: list[ClaimedRow],
        exc: Exception,
    ) -> None:
        """A once-per-group source fetch failed.

        Permanent (source gone) → cancel the non-deleted rows (nothing to deliver).
        Transient → back them off, but still honour the per-row attempt cap so a
        source that is *persistently* unfetchable eventually terminalizes instead of the
        run looping forever (its view never completing, blocking clean restarts). Such a
        terminal is recorded ``confirmed_dead=False`` — the source's failure is not the
        destination's fault, so it must never feed the auto-disable streak.
        """
        non_deleted = [r for r in rows if not r.deleted]
        if classify_error(exc) is ErrorClass.PERMANENT:
            health_logger.warning(
                "Mirror source message %s unfetchable (%s) — cancelling %d dest(s).",
                rows[0].src_msg_id,
                type(exc).__name__,
                len(non_deleted),
            )
            for row in non_deleted:
                self._record_cancelled(view, row)
            return

        ref = reference_code(identity_for_exc(exc))
        err_class = classify_error(exc)
        for row in non_deleted:
            attempts = row.attempts + 1
            is_send = row.dest_msg_id is None
            cap = (
                cfg.mirror_send_max_attempts
                if is_send
                else cfg.mirror_edit_max_attempts
            )
            if attempts >= cap:
                self._emit(
                    self._outcome(
                        OutcomeKind.TERMINAL,
                        row,
                        attempts=attempts,
                        error_ref=ref,
                        error_class=err_class.name,
                        error_msg=str(exc)[:256],
                        confirmed_dead=False,
                    )
                )
                if view is not None:
                    view.on_failed(
                        row.dest_ch_id, RunFailure(ref, err_class, str(exc), False)
                    )
            else:
                self._record_transient(view, row, exc, ref, attempts=attempts)

    async def _deliver_row(
        self,
        sem: aio.Semaphore,
        view: RunView | None,
        row: ClaimedRow,
        msg: h.Message | None,
        role_map: _RoleMap,
    ) -> None:
        """Converge one destination row.

        Only the actual Discord send/edit/delete holds a concurrency slot. Failure
        handling (which may run a multi-REST dead-probe) and the non-fatal crosspost
        (which may back off for tens of seconds) run *outside* the semaphore, so a
        mass-failure or a flaky crosspost can't starve the healthy sends of slots.
        """
        outcome: DeliveryOutcome | None = None
        failure: Exception | None = None
        crosspost: tuple[int, int] | None = None
        async with sem:
            # Re-check cancellation after acquiring the slot: a cancel that fired while
            # we waited must not send anything Discord-side (there is no await between
            # this check and the API call).
            if view is not None and view.cancel_requested and not row.deleted:
                self._record_cancelled(view, row)
                return
            try:
                outcome, crosspost = await self._do_delivery(row, msg, role_map)
            except Exception as e:
                failure = e
        if failure is not None:
            await self._handle_row_failure(view, row, failure)
            return
        assert outcome is not None
        if crosspost is not None:
            await _crosspost_one(self._bot_or_raise(), *crosspost)
        self._emit(outcome)
        if view is not None:
            if outcome.kind is OutcomeKind.CANCELLED:
                view.on_cancelled(row.dest_ch_id)
            else:
                view.on_delivered(row.dest_ch_id)

    async def _do_delivery(
        self,
        row: ClaimedRow,
        msg: h.Message | None,
        role_map: _RoleMap,
    ) -> tuple[DeliveryOutcome, tuple[int, int] | None]:
        """Perform one row's Discord op; return its outcome + optional crosspost work.

        The crosspost target ``(ch_id, dest_msg_id)`` is returned (not done here) for a
        fresh send to a news channel, so the caller can crosspost outside the slot.
        """
        bot = self._bot_or_raise()
        if row.deleted:
            if row.dest_msg_id is None:
                # Never delivered → nothing to delete Discord-side.
                return self._outcome(OutcomeKind.CANCELLED, row), None
            await _delete_one(bot, row.dest_ch_id, row.dest_msg_id)
            return self._outcome(OutcomeKind.DELETE_SUCCESS, row), None
        assert msg is not None
        if row.dest_msg_id is None:
            new_id, is_news = await _send_one(bot, msg, row.dest_ch_id, role_map)
            crosspost = (row.dest_ch_id, new_id) if is_news else None
            return self._outcome(
                OutcomeKind.SUCCESS, row, dest_msg_id=new_id
            ), crosspost
        await edit_one(bot, msg, row.dest_ch_id, row.dest_msg_id, role_map)
        return self._outcome(
            OutcomeKind.SUCCESS, row, dest_msg_id=row.dest_msg_id
        ), None

    def _bot_or_raise(self) -> CachedFetchBot:
        """The wired bot, or a clear error if a delivery ran before ``start()``.

        Replaces scattered ``assert self._bot is not None`` guards, which ``python -OO``
        (the production entrypoint) strips — turning the invariant into a no-op and
        letting a stray ``None`` surface as a misclassified, retried ``AttributeError``.
        """
        if self._bot is None:
            raise RuntimeError("MirrorWorker used before start() wired a bot")
        return self._bot

    async def _handle_row_failure(
        self, view: RunView | None, row: ClaimedRow, exc: Exception
    ) -> None:
        """Classify a delivery exception → transient backoff or terminal FAILED.

        Terminal when the error is PERMANENT or the per-op attempt cap is hit; a
        PERMANENT terminal also runs the (never-raising) dead-probe that gates
        auto-disable.
        Runs outside the delivery semaphore (see :meth:`_deliver_row`).
        """
        ref = reference_code(identity_for_exc(exc))
        err_class = classify_error(exc)
        attempts = row.attempts + 1
        is_send = row.dest_msg_id is None and not row.deleted
        cap = cfg.mirror_send_max_attempts if is_send else cfg.mirror_edit_max_attempts
        if err_class is ErrorClass.PERMANENT or attempts >= cap:
            confirmed_dead = False
            if err_class is ErrorClass.PERMANENT and cfg.disable_bad_channels:
                confirmed_dead = await self._probe_dead(row.dest_ch_id)
            self._emit(
                self._outcome(
                    OutcomeKind.TERMINAL,
                    row,
                    attempts=attempts,
                    error_ref=ref,
                    error_class=err_class.name,
                    error_msg=str(exc)[:256],
                    confirmed_dead=confirmed_dead,
                )
            )
            if view is not None:
                view.on_failed(
                    row.dest_ch_id,
                    RunFailure(ref, err_class, str(exc), confirmed_dead),
                )
        else:
            self._record_transient(view, row, exc, ref, attempts=attempts)

    def _record_transient(
        self,
        view: RunView | None,
        row: ClaimedRow,
        exc: Exception,
        ref: str,
        *,
        attempts: int,
    ) -> None:
        """Buffer a TRANSIENT outcome (re-scheduled with a randomised backoff) + note
        the retry on the view."""
        due = dt.datetime.now(tz=dt.UTC) + dt.timedelta(
            seconds=randint(cfg.mirror_retry_min, cfg.mirror_retry_max)
        )
        self._emit(
            self._outcome(
                OutcomeKind.TRANSIENT,
                row,
                attempts=attempts,
                due_at=due,
                error_ref=ref,
                error_class=classify_error(exc).name,
                error_msg=str(exc)[:256],
            )
        )
        if view is not None:
            view.on_transient(row.dest_ch_id)

    def _record_cancelled(self, view: RunView | None, row: ClaimedRow) -> None:
        """Buffer a CANCELLED outcome + note it on the view."""
        self._emit(self._outcome(OutcomeKind.CANCELLED, row))
        if view is not None:
            view.on_cancelled(row.dest_ch_id)

    async def _probe_dead(self, dest_ch_id: int) -> bool:
        """Confirm (WITHOUT sending) whether a permanently-failing dest is genuinely
        dead. Never raises: a flaky probe biases toward *not* confirming (no disable).
        """
        bot = self._bot_or_raise()
        try:
            verdict = await utils.confirm_dest_unsendable(bot, dest_ch_id)
        except Exception:
            return False
        return verdict in (
            utils.DestVerdict.CONFIRMED_UNSENDABLE,
            utils.DestVerdict.CONFIRMED_GONE,
        )

    @staticmethod
    def _outcome(
        kind: OutcomeKind, row: ClaimedRow, **kwargs: t.Any
    ) -> DeliveryOutcome:
        """Build a :class:`DeliveryOutcome` stamped with the claimed row's identity +
        the version this attempt delivered (the flusher's guard compares it)."""
        return DeliveryOutcome(
            kind=kind,
            src_msg_id=row.src_msg_id,
            dest_ch_id=row.dest_ch_id,
            version=row.desired_version,
            **kwargs,
        )

    def _emit(self, outcome: DeliveryOutcome) -> None:
        """Buffer an outcome and wake the flusher."""
        self._buffer.append(outcome)
        if self._buffer_event is not None:
            self._buffer_event.set()

    # -- flusher -----------------------------------------------------------

    async def _flusher_loop(self) -> None:
        assert self._buffer_event is not None
        backoff = 5
        while self._running:
            await self._buffer_event.wait()
            self._buffer_event.clear()
            ok = await self._flush_once()
            if ok:
                backoff = 5
            else:
                # Re-queued at the front; retry after a capped backoff.
                self._buffer_event.set()
                await aio.sleep(backoff)
                backoff = min(_FLUSH_MAX_BACKOFF, backoff * 2)

    async def _flush_once(self) -> bool:
        """Swap the buffer and write it back in one transaction. New outcomes accrue in
        the fresh buffer during the write. On failure the batch is re-queued at the
        front and ``False`` returned."""
        if not self._buffer:
            return True
        batch, self._buffer = self._buffer, []
        try:
            await MirrorDelivery.flush_outcomes(batch)
        except Exception:
            self._buffer[0:0] = batch
            logging.exception("mirror flush of %d outcome(s) failed", len(batch))
            return False
        return True

    # -- completion --------------------------------------------------------

    async def _reconcile_views(self) -> None:
        """Finalize runs the ledger says are done — the authoritative completion signal.

        For every active (non-finalized) view: a superseded handover finalizes at once
        (its successor reports); otherwise the run finalizes exactly when the ledger has
        **no** non-terminal rows left for its source message. This — not in-memory set
        accounting — is what correctly finalizes a run whose completion the view can't
        see itself: a source delete that only cancelled rows, a run whose outcomes
        landed while its view was momentarily unregistered, or a superseding edit that
        credited stale in-flight deliveries (the guard bounced those rows back to
        PENDING, so the ledger still shows work and the view is *not* prematurely
        finalized). One grouped COUNT over the active sources, so it is cheap.
        """
        active = [
            v
            for v in list(self.run_views.values())
            if not v.finalized and not v._finalizing
        ]
        if not active:
            return
        need_ledger = [v for v in active if not v.superseded]
        counts: dict[int, int] = {}
        if need_ledger:
            counts = await MirrorDelivery.non_terminal_counts(
                [v.src_msg_id for v in need_ledger]
            )
        for view in active:
            if view.superseded or counts.get(view.src_msg_id, 0) == 0:
                self._begin_finalize(view)

    def maybe_finalize(self, view: RunView) -> None:
        """Finalize a run *now* if it is a superseded handover or in-memory-complete.

        The fast path used by the gateway handlers (an edit/delete superseding a live
        run) and the unit tests. Steady-state completion is driven by the ledger
        reconcile in the claim loop (:meth:`_reconcile_views`), which is authoritative;
        this never finalizes a run the reconcile wouldn't, it just avoids waiting a poll
        cycle for the obvious cases.
        """
        if view.superseded or view.superseded_by_edit or view.is_complete:
            self._begin_finalize(view)

    def _begin_finalize(self, view: RunView) -> None:
        """Spawn the one-shot run-end task for a view (idempotent)."""
        if view._finalizing or view.finalized:
            return
        view._finalizing = True
        task = aio.create_task(self._run_end(view))
        self._end_tasks.add(task)
        task.add_done_callback(self._end_tasks.discard)

    async def _run_end(self, view: RunView) -> None:
        """Run the (optional) run-end hook once, then mark the view finalized.

        ``finalized`` is set even if the hook raises, so a hook failure can't wedge the
        run's progress card open forever (it would never take its final render)."""
        try:
            if self._run_end_hook is not None:
                await self._run_end_hook(view)
        except Exception:
            logging.exception(
                "mirror run-end hook failed for source message %s", view.src_msg_id
            )
        finally:
            view.finalized = True

    # -- startup backlog recovery -----------------------------------------

    async def _recover_backlog(self) -> None:
        """Register synthetic recovery views (and progress cards) for any non-terminal
        rows left by a previous process, so a post-restart backlog is visible — not
        silently converged. Metrics are approximate (elapsed restarts from now)."""
        try:
            backlog = await MirrorDelivery.non_terminal_backlog()
        except Exception:
            logging.exception("mirror backlog recovery query failed")
            return
        for src_msg_id, src_ch_id, count, any_deleted, any_unsent in backlog:
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
                total=count,
                start_time=perf_counter(),
            )
            self.register_view(view)
            if self._progress_starter is not None:
                try:
                    await self._progress_starter(view)
                except Exception:
                    logging.exception(
                        "failed to start recovery progress card for %s", src_msg_id
                    )
        if backlog:
            logging.info(
                "Mirror backlog recovery: %d source message(s) with pending work.",
                len(backlog),
            )


# The single per-process worker instance.
mirror_worker = MirrorWorker()
