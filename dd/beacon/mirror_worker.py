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

# Callback the gateway handlers / recovery use to spin up a progress card for a view.
ProgressStarter = collections.abc.Callable[
    ["RunView"], collections.abc.Awaitable[None]
]
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


async def _send_one(
    bot: CachedFetchBot,
    msg: h.Message,
    ch_id: int,
    role_ping_per_ch_id: collections.abc.Mapping[int, int | None],
) -> int:
    """Send ``msg`` to channel ``ch_id`` (rate-limited) and return the new msg id.

    Crossposts as non-fatal post-success work for announcement channels (the "already
    crossposted" 400 is treated as success/ignored).
    """
    channel = await bot.fetch_channel(ch_id)
    if not isinstance(channel, h.TextableChannel):
        raise ValueError("Channel is not textable")

    if _is_cv2(msg):
        components = _cv2_components_for(msg, ch_id, role_ping_per_ch_id)
        async with rate_limiter:
            mirrored_msg = await channel.send(
                components=components,
                flags=h.MessageFlag.IS_COMPONENTS_V2,
                role_mentions=True,
            )
    else:
        msg_content = add_role_ping_to_msg(msg.content, ch_id, role_ping_per_ch_id)
        async with rate_limiter:
            # Components are no longer mirrored for embed messages, so admin buttons on
            # the main server's messages are not carried to destinations.
            mirrored_msg = await channel.send(
                msg_content,
                attachments=msg.attachments,
                embeds=msg.embeds,
                role_mentions=True,
            )

    if isinstance(channel, h.GuildNewsChannel):
        # Crosspost the mirrored message too. Non-fatal: a crosspost failure does not
        # fail the send (the message is already sent).
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


async def edit_one(
    bot: CachedFetchBot,
    msg: h.Message,
    ch_id: int,
    dest_msg_id: int,
    role_ping_per_ch_id: collections.abc.Mapping[int, int | None],
) -> int:
    """Edit the recorded dest message in ``ch_id`` to match ``msg``; return its id."""
    async with rate_limiter:
        dest_msg = await bot.fetch_message(ch_id, dest_msg_id)
    if _is_cv2(msg):
        components = _cv2_components_for(msg, ch_id, role_ping_per_ch_id)
        async with rate_limiter:
            await dest_msg.edit(
                components=components,
                flags=h.MessageFlag.IS_COMPONENTS_V2,
                role_mentions=True,
            )
        return dest_msg_id
    msg_content = add_role_ping_to_msg(msg.content, ch_id, role_ping_per_ch_id)
    async with rate_limiter:
        await dest_msg.edit(
            msg_content,
            attachments=msg.attachments,
            embeds=msg.embeds,
            role_mentions=True,
        )
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
        self._end_tasks: set[aio.Task[None]] = set()
        self.run_views: dict[int, RunView] = {}
        self._progress_starter: ProgressStarter | None = None
        self._run_end_hook: RunEndHook | None = None

    # -- lifecycle ---------------------------------------------------------

    async def start(
        self,
        bot: CachedFetchBot,
        *,
        progress_starter: ProgressStarter | None = None,
        run_end_hook: RunEndHook | None = None,
    ) -> None:
        """Wire the worker to ``bot``, recover any post-restart backlog, and spin up the
        claim + flusher loops."""
        self._bot = bot
        self._progress_starter = progress_starter
        self._run_end_hook = run_end_hook
        self._wake = aio.Event()
        self._buffer_event = aio.Event()
        self._running = True
        await self._recover_backlog()
        self._main_task = aio.create_task(self._main_loop())
        self._flusher_task = aio.create_task(self._flusher_loop())

    async def stop(self) -> None:
        """Signal the loops to exit and await their teardown."""
        self._running = False
        if self._wake is not None:
            self._wake.set()
        if self._buffer_event is not None:
            self._buffer_event.set()
        tasks = [
            task
            for task in (self._main_task, self._flusher_task, *self._end_tasks)
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await aio.gather(*tasks, return_exceptions=True)

    def nudge(self) -> None:
        """Wake the claim loop early (a gateway handler just enqueued work)."""
        if self._wake is not None:
            self._wake.set()

    # -- run-view registry -------------------------------------------------

    def register_view(self, view: RunView) -> None:
        self.run_views[view.src_msg_id] = view

    def get_view(self, src_msg_id: int) -> RunView | None:
        return self.run_views.get(src_msg_id)

    def evict(self, src_msg_id: int) -> None:
        self.run_views.pop(src_msg_id, None)

    # -- claim loop --------------------------------------------------------

    async def _main_loop(self) -> None:
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
            if not batch:
                with contextlib.suppress(TimeoutError):
                    await aio.wait_for(
                        self._wake.wait(), timeout=cfg.mirror_poll_interval
                    )
                continue
            await self.process(batch)
            self._check_completions(batch)

    async def process(self, batch: list[ClaimedRow]) -> None:
        """Converge a claimed batch: group by source message, deliver each row.

        A single semaphore bounds in-flight Discord calls across the whole batch; the
        global rate limiter bounds request rate.
        """
        sem = aio.Semaphore(cfg.mirror_max_concurrency)
        groups: dict[int, list[ClaimedRow]] = defaultdict(list)
        for row in batch:
            groups[row.src_msg_id].append(row)
        await aio.gather(
            *(self._process_group(sem, rows) for rows in groups.values())
        )

    async def _process_group(
        self, sem: aio.Semaphore, rows: list[ClaimedRow]
    ) -> None:
        src_msg_id = rows[0].src_msg_id
        src_ch_id = rows[0].src_ch_id
        view = self.get_view(src_msg_id)

        needs_source = any(not r.deleted for r in rows)
        msg: h.Message | None = None
        role_map: collections.abc.Mapping[int, int | None] = {}
        if needs_source:
            try:
                assert self._bot is not None
                msg = await self._bot.rest.fetch_message(src_ch_id, src_msg_id)
                msg.embeds = utils.filter_discord_autoembeds(msg)
                role_map = await MirroredChannel.fetch_mirror_and_role_mention_id(
                    src_ch_id
                )
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

    async def _handle_group_source_failure(
        self,
        view: RunView | None,
        rows: list[ClaimedRow],
        exc: Exception,
    ) -> None:
        """A once-per-group source fetch failed: transient → back the non-deleted rows
        off; permanent (source gone) → cancel them (nothing to deliver)."""
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
        else:
            ref = reference_code(identity_for_exc(exc))
            for row in non_deleted:
                self._record_transient(view, row, exc, ref)

    async def _deliver_row(
        self,
        sem: aio.Semaphore,
        view: RunView | None,
        row: ClaimedRow,
        msg: h.Message | None,
        role_map: collections.abc.Mapping[int, int | None],
    ) -> None:
        async with sem:
            # Re-check cancellation after acquiring the slot: a cancel that fired while
            # we waited must not send anything Discord-side (there is no await between
            # this check and the API call).
            if view is not None and view.cancel_requested and not row.deleted:
                self._record_cancelled(view, row)
                return
            try:
                outcome = await self._do_delivery(row, msg, role_map)
            except Exception as e:
                await self._handle_row_failure(view, row, e)
                return
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
        role_map: collections.abc.Mapping[int, int | None],
    ) -> DeliveryOutcome:
        assert self._bot is not None
        bot = self._bot
        if row.deleted:
            if row.dest_msg_id is None:
                # Never delivered → nothing to delete Discord-side.
                return self._outcome(OutcomeKind.CANCELLED, row)
            await _delete_one(bot, row.dest_ch_id, row.dest_msg_id)
            return self._outcome(OutcomeKind.DELETE_SUCCESS, row)
        assert msg is not None
        if row.dest_msg_id is None:
            new_id = await _send_one(bot, msg, row.dest_ch_id, role_map)
            return self._outcome(OutcomeKind.SUCCESS, row, dest_msg_id=new_id)
        await edit_one(bot, msg, row.dest_ch_id, row.dest_msg_id, role_map)
        return self._outcome(OutcomeKind.SUCCESS, row, dest_msg_id=row.dest_msg_id)

    async def _handle_row_failure(
        self, view: RunView | None, row: ClaimedRow, exc: Exception
    ) -> None:
        ref = reference_code(identity_for_exc(exc))
        err_class = classify_error(exc)
        attempts = row.attempts + 1
        is_send = row.dest_msg_id is None and not row.deleted
        cap = (
            cfg.mirror_send_max_attempts if is_send else cfg.mirror_edit_max_attempts
        )
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
        attempts: int | None = None,
    ) -> None:
        attempts = row.attempts + 1 if attempts is None else attempts
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
        self._emit(self._outcome(OutcomeKind.CANCELLED, row))
        if view is not None:
            view.on_cancelled(row.dest_ch_id)

    async def _probe_dead(self, dest_ch_id: int) -> bool:
        """Confirm (WITHOUT sending) whether a permanently-failing dest is genuinely
        dead. Never raises: a flaky probe biases toward *not* confirming (no disable).
        """
        assert self._bot is not None
        try:
            verdict = await utils.confirm_dest_unsendable(self._bot, dest_ch_id)
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
        return DeliveryOutcome(
            kind=kind,
            src_msg_id=row.src_msg_id,
            dest_ch_id=row.dest_ch_id,
            version=row.desired_version,
            **kwargs,
        )

    def _emit(self, outcome: DeliveryOutcome) -> None:
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

    def _check_completions(self, batch: list[ClaimedRow]) -> None:
        for src_msg_id in {row.src_msg_id for row in batch}:
            view = self.run_views.get(src_msg_id)
            if view is not None:
                self.maybe_finalize(view)

    def maybe_finalize(self, view: RunView) -> None:
        """Fire the run-end hook once a run has resolved every destination."""
        if view._finalizing or view.finalized or not view.is_complete:
            return
        view._finalizing = True
        task = aio.create_task(self._run_end(view))
        self._end_tasks.add(task)
        task.add_done_callback(self._end_tasks.discard)

    async def _run_end(self, view: RunView) -> None:
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
