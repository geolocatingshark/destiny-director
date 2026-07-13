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

"""Unit tests for the mirror convergence worker's delivery path (no DB, no real bot).

The Discord primitives (_send_one / edit_one / _delete_one / _crosspost_one) and the
source fetch are stubbed; these pin op selection per row shape, one source fetch per
group + the per-version source cache, the transient/terminal decision (attempt caps,
backoff), permanent source-fetch handling, and durable crosspost convergence."""

import asyncio as aio
import typing as t
from types import SimpleNamespace
from unittest.mock import AsyncMock

import hikari as h
import pytest

from dd.beacon import mirror_worker as mw
from dd.common.bot import CachedFetchBot
from dd.common.schemas import (
    CrosspostState,
    DeliveryState,
    MirroredChannel,
    OutcomeKind,
    PickedRow,
)
from dd.common.utils import ErrorClass

pytestmark = pytest.mark.asyncio


def _row(
    dest_ch_id,
    *,
    dest_msg_id=None,
    deleted=False,
    attempts=0,
    src_msg_id=1,
    desired_version=1,
    state=DeliveryState.PENDING.value,
    crosspost_state=CrosspostState.NOT_APPLICABLE.value,
):
    return PickedRow(
        src_msg_id=src_msg_id,
        dest_ch_id=dest_ch_id,
        src_ch_id=5,
        dest_msg_id=dest_msg_id,
        desired_version=desired_version,
        deleted=deleted,
        attempts=attempts,
        state=state,
        crosspost_state=crosspost_state,
    )


def _crosspost_pick(dest_ch_id, *, dest_msg_id=999, attempts=0):
    return _row(
        dest_ch_id,
        dest_msg_id=dest_msg_id,
        attempts=attempts,
        state=DeliveryState.DELIVERED.value,
        crosspost_state=CrosspostState.PENDING.value,
    )


def _fake_msg():
    return SimpleNamespace(
        content="hello", embeds=[], id=1, channel_id=5, flags=h.MessageFlag.NONE
    )


def _worker(monkeypatch, *, fetch_message=None):
    """A worker with a stub bot + stubbed role-map DB read (primitives stubbed)."""
    fetch = fetch_message or AsyncMock(return_value=_fake_msg())
    bot = SimpleNamespace(rest=SimpleNamespace(fetch_message=fetch))
    monkeypatch.setattr(
        MirroredChannel, "fetch_mirror_and_role_mention_id", AsyncMock(return_value={})
    )
    w = mw.MirrorWorker()
    w._bot = t.cast(CachedFetchBot, bot)
    return w


def _stub_primitives(monkeypatch, *, send=None, edit=None, delete=None, crosspost=None):
    monkeypatch.setattr(mw, "_send_one", send or AsyncMock(return_value=(7777, False)))
    monkeypatch.setattr(mw, "edit_one", edit or AsyncMock(return_value=0))
    monkeypatch.setattr(mw, "_delete_one", delete or AsyncMock(return_value=None))
    monkeypatch.setattr(mw, "_crosspost_one", crosspost or AsyncMock(return_value=None))


def _kinds(outcomes) -> dict[int, OutcomeKind]:
    return {o.dest_ch_id: o.kind for o in outcomes}


# -- op selection ------------------------------------------------------------


async def test_op_selection_per_row_shape(monkeypatch):
    send, edit, delete = AsyncMock(return_value=(7777, False)), AsyncMock(), AsyncMock()
    _stub_primitives(monkeypatch, send=send, edit=edit, delete=delete)
    w = _worker(monkeypatch)
    batch = [
        _row(10),  # no dest, not deleted → send
        _row(11, dest_msg_id=111),  # has dest, not deleted → edit
        _row(12, dest_msg_id=112, deleted=True),  # has dest, deleted → delete
        _row(13, deleted=True),  # deleted, no dest → cancelled (nothing to delete)
    ]
    outcomes = await w._process(batch)
    send.assert_awaited_once()
    edit.assert_awaited_once()
    delete.assert_awaited_once()
    assert _kinds(outcomes) == {
        10: OutcomeKind.SUCCESS,
        11: OutcomeKind.SUCCESS,
        12: OutcomeKind.DELETE_SUCCESS,
        13: OutcomeKind.CANCELLED,
    }
    # The freshly-sent dest records its new id (a dest id once observed is always kept).
    sent = next(o for o in outcomes if o.dest_ch_id == 10)
    assert sent.dest_msg_id == 7777


async def test_one_source_fetch_per_group(monkeypatch):
    fetch = AsyncMock(return_value=_fake_msg())
    _stub_primitives(monkeypatch)
    w = _worker(monkeypatch, fetch_message=fetch)
    await w._process([_row(10), _row(11), _row(12)])  # same src_msg_id
    fetch.assert_awaited_once()


async def test_all_deleted_group_skips_source_fetch(monkeypatch):
    fetch = AsyncMock(return_value=_fake_msg())
    _stub_primitives(monkeypatch)
    w = _worker(monkeypatch, fetch_message=fetch)
    await w._process([_row(10, dest_msg_id=100, deleted=True)])
    fetch.assert_not_awaited()


# -- failure classification --------------------------------------------------


async def test_transient_failure_backs_off(monkeypatch):
    send = AsyncMock(side_effect=TimeoutError("5xx"))
    _stub_primitives(monkeypatch, send=send)
    monkeypatch.setattr(mw, "classify_error", lambda e: ErrorClass.TRANSIENT)
    w = _worker(monkeypatch)
    (outcome,) = await w._process([_row(10)])
    assert outcome.kind is OutcomeKind.TRANSIENT
    assert outcome.attempts == 1
    assert outcome.due_at is not None  # scheduled for a later retry


async def test_send_attempt_cap_is_three(monkeypatch):
    _stub_primitives(monkeypatch, send=AsyncMock(side_effect=TimeoutError()))
    monkeypatch.setattr(mw, "classify_error", lambda e: ErrorClass.TRANSIENT)
    w = _worker(monkeypatch)
    # attempts already 2 → this attempt is the 3rd (== send cap) → terminal.
    (outcome,) = await w._process([_row(10, attempts=2)])
    assert outcome.kind is OutcomeKind.TERMINAL


async def test_edit_attempt_cap_is_two(monkeypatch):
    _stub_primitives(monkeypatch, edit=AsyncMock(side_effect=TimeoutError()))
    monkeypatch.setattr(mw, "classify_error", lambda e: ErrorClass.TRANSIENT)
    w = _worker(monkeypatch)
    # An edit (dest_msg_id set) with attempts 1 → 2nd attempt (== edit cap) → terminal.
    (outcome,) = await w._process([_row(10, dest_msg_id=100, attempts=1)])
    assert outcome.kind is OutcomeKind.TERMINAL


async def test_permanent_failure_is_terminal(monkeypatch):
    _stub_primitives(monkeypatch, send=AsyncMock(side_effect=RuntimeError("perm")))
    monkeypatch.setattr(mw, "classify_error", lambda e: ErrorClass.PERMANENT)
    w = _worker(monkeypatch)
    (outcome,) = await w._process([_row(10)])
    assert outcome.kind is OutcomeKind.TERMINAL
    assert outcome.error_class == ErrorClass.PERMANENT.name


# -- source failure ----------------------------------------------------------


async def test_permanent_source_fetch_cancels_group(monkeypatch):
    fetch = AsyncMock(side_effect=RuntimeError("source gone"))
    send = AsyncMock()
    _stub_primitives(monkeypatch, send=send)
    monkeypatch.setattr(mw, "classify_error", lambda e: ErrorClass.PERMANENT)
    w = _worker(monkeypatch, fetch_message=fetch)
    outcomes = await w._process([_row(10), _row(11)])
    send.assert_not_awaited()
    assert _kinds(outcomes) == {10: OutcomeKind.CANCELLED, 11: OutcomeKind.CANCELLED}


async def test_transient_source_fetch_backs_off_group(monkeypatch):
    fetch = AsyncMock(side_effect=TimeoutError("5xx"))
    _stub_primitives(monkeypatch)
    monkeypatch.setattr(mw, "classify_error", lambda e: ErrorClass.TRANSIENT)
    w = _worker(monkeypatch, fetch_message=fetch)
    outcomes = await w._process([_row(10), _row(11)])
    assert _kinds(outcomes) == {10: OutcomeKind.TRANSIENT, 11: OutcomeKind.TRANSIENT}


async def test_transient_source_fetch_terminalizes_at_cap(monkeypatch):
    fetch = AsyncMock(side_effect=TimeoutError("5xx"))
    _stub_primitives(monkeypatch)
    monkeypatch.setattr(mw, "classify_error", lambda e: ErrorClass.TRANSIENT)
    w = _worker(monkeypatch, fetch_message=fetch)
    # attempts already 2 → this (3rd) attempt hits the send cap → TERMINAL.
    (outcome,) = await w._process([_row(10, attempts=2)])
    assert outcome.kind is OutcomeKind.TERMINAL


# -- source caching ----------------------------------------------------------


async def test_source_fetched_once_across_batches(monkeypatch):
    fetch = AsyncMock(return_value=_fake_msg())
    _stub_primitives(monkeypatch)
    w = _worker(monkeypatch, fetch_message=fetch)
    # Two pick batches for the same source + version: the second is served from the
    # per-(src, version) cache, so the source is fetched only once.
    await w._process([_row(10)])
    await w._process([_row(11)])
    fetch.assert_awaited_once()


async def test_source_refetched_on_version_bump(monkeypatch):
    fetch = AsyncMock(return_value=_fake_msg())
    _stub_primitives(monkeypatch)
    w = _worker(monkeypatch, fetch_message=fetch)
    await w._process([_row(10)])  # version 1
    await w._process([_row(11, desired_version=2)])  # version 2 → cache miss → refetch
    assert fetch.await_count == 2


# -- durable crosspost -------------------------------------------------------


async def test_fresh_news_send_marks_crosspost_pending(monkeypatch):
    # _send_one reporting is_news=True stamps crosspost_pending on the SUCCESS outcome.
    _stub_primitives(monkeypatch, send=AsyncMock(return_value=(7777, True)))
    w = _worker(monkeypatch)
    (outcome,) = await w._process([_row(10)])
    assert outcome.kind is OutcomeKind.SUCCESS
    assert outcome.crosspost_pending is True


async def test_crosspost_pick_success_is_done(monkeypatch):
    crosspost = AsyncMock(return_value=None)
    _stub_primitives(monkeypatch, crosspost=crosspost)
    w = _worker(monkeypatch)
    (outcome,) = await w._process([_crosspost_pick(10)])
    crosspost.assert_awaited_once()
    assert outcome.kind is OutcomeKind.CROSSPOST_DONE


async def test_crosspost_pick_transient_retries(monkeypatch):
    _stub_primitives(monkeypatch, crosspost=AsyncMock(side_effect=TimeoutError("5xx")))
    monkeypatch.setattr(mw, "classify_error", lambda e: ErrorClass.TRANSIENT)
    w = _worker(monkeypatch)
    (outcome,) = await w._process([_crosspost_pick(10, attempts=0)])
    assert outcome.kind is OutcomeKind.CROSSPOST_RETRY
    assert outcome.due_at is not None


async def test_crosspost_pick_gives_up_at_cap(monkeypatch):
    _stub_primitives(monkeypatch, crosspost=AsyncMock(side_effect=TimeoutError("5xx")))
    monkeypatch.setattr(mw, "classify_error", lambda e: ErrorClass.TRANSIENT)
    w = _worker(monkeypatch)
    # attempts already at the cap-1 → this attempt exhausts it → DONE (best-effort).
    (outcome,) = await w._process(
        [_crosspost_pick(10, attempts=mw._CROSSPOST_MAX_ATTEMPTS - 1)]
    )
    assert outcome.kind is OutcomeKind.CROSSPOST_DONE


async def test_process_mixes_delivery_and_crosspost(monkeypatch):
    send = AsyncMock(return_value=(7777, False))
    crosspost = AsyncMock(return_value=None)
    _stub_primitives(monkeypatch, send=send, crosspost=crosspost)
    w = _worker(monkeypatch)
    outcomes = await w._process([_row(10), _crosspost_pick(20)])
    assert _kinds(outcomes) == {
        10: OutcomeKind.SUCCESS,
        20: OutcomeKind.CROSSPOST_DONE,
    }


# -- stop() draining ---------------------------------------------------------


async def test_stop_drains_the_in_flight_batch(monkeypatch):
    # stop() called mid-batch must let the current pick→process→flush finish, so an
    # already-sent row is flushed (dest_msg_id recorded) and cannot re-send on restart.
    w = _worker(monkeypatch)

    picks = {"n": 0}

    async def pick(_batch_size, **_kw):
        picks["n"] += 1
        return [_row(200)] if picks["n"] == 1 else []

    monkeypatch.setattr(mw.MirrorDelivery, "pick_batch", pick)

    processing = aio.Event()
    flushed = aio.Event()

    async def process(_batch):
        processing.set()
        await aio.sleep(0.05)  # simulate the in-flight Discord sends
        return ["outcome"]

    async def flush(outcomes):
        assert outcomes == ["outcome"]
        flushed.set()

    monkeypatch.setattr(w, "_process", process)
    monkeypatch.setattr(w, "_flush", flush)

    await w.start(w._bot)
    await processing.wait()  # we're mid-batch
    await w.stop()  # should drain, not cancel

    assert flushed.is_set()  # the in-flight batch was flushed before exit
    assert w._main_task is None


async def test_stop_force_cancels_when_drain_stalls(monkeypatch):
    # If the drain can't finish in time (e.g. a stuck flush), stop() force-cancels
    # rather than hanging shutdown; those rows just recover on the next startup.
    w = _worker(monkeypatch)

    async def pick(_batch_size, **_kw):
        return [_row(200)]

    monkeypatch.setattr(mw.MirrorDelivery, "pick_batch", pick)

    entered = aio.Event()

    async def process(_batch):
        entered.set()
        await aio.sleep(100)  # never completes within the drain window
        return []

    monkeypatch.setattr(w, "_process", process)
    monkeypatch.setattr(w, "_flush", AsyncMock())

    await w.start(w._bot)
    await entered.wait()
    await w.stop(drain_timeout=0.05)  # too short → force-cancel

    assert w._main_task is None  # returned without hanging


# -- source-cache eviction ---------------------------------------------------


async def test_evict_resolved_sources_drops_only_resolved(monkeypatch):
    # After a batch, a source whose fan-out has resolved is evicted from the content
    # cache; one that still has PENDING delivery work is kept.
    w = _worker(monkeypatch)
    w._source_cache = {1: (1, _fake_msg(), {}), 2: (1, _fake_msg(), {})}
    monkeypatch.setattr(
        mw.MirrorDelivery,
        "sources_needing_source_content",
        AsyncMock(return_value={1}),
    )

    await w._evict_resolved_sources([_row(10, src_msg_id=1), _row(20, src_msg_id=2)])

    assert set(w._source_cache) == {1}  # 2 resolved → evicted, 1 kept


async def test_evict_resolved_sources_skips_query_when_nothing_cached(monkeypatch):
    w = _worker(monkeypatch)
    w._source_cache = {}  # batch source 1 is not cached (e.g. a crosspost/delete batch)
    query = AsyncMock(return_value=set())
    monkeypatch.setattr(
        mw.MirrorDelivery, "sources_needing_source_content", query
    )

    await w._evict_resolved_sources([_row(10, src_msg_id=1)])

    query.assert_not_awaited()


async def test_evict_resolved_sources_survives_a_query_failure(monkeypatch):
    # The eviction query is called unguarded in the loop; a failure must leave the cache
    # intact and never escape (a raise here would kill the worker).
    w = _worker(monkeypatch)
    w._source_cache = {1: (1, _fake_msg(), {}), 2: (1, _fake_msg(), {})}

    async def boom(_srcs):
        raise RuntimeError("db blip")

    monkeypatch.setattr(mw.MirrorDelivery, "sources_needing_source_content", boom)

    await w._evict_resolved_sources([_row(10, src_msg_id=1)])  # must not raise

    assert set(w._source_cache) == {1, 2}  # left intact


# -- loop resilience (a dead loop silently stops ALL mirroring) ---------------


async def test_loop_survives_a_pick_failure_and_recovers(monkeypatch):
    monkeypatch.setattr(mw.aio, "sleep", AsyncMock())  # instant backoff
    w = _worker(monkeypatch)
    picks = {"n": 0}
    processed = aio.Event()

    async def pick(_bs, **_kw):
        picks["n"] += 1
        if picks["n"] == 1:
            raise RuntimeError("db blip")
        return [_row(200)] if picks["n"] == 2 else []

    monkeypatch.setattr(mw.MirrorDelivery, "pick_batch", pick)

    async def process(_batch):
        processed.set()
        return []

    monkeypatch.setattr(w, "_process", process)
    monkeypatch.setattr(w, "_flush", AsyncMock())
    monkeypatch.setattr(w, "_evict_resolved_sources", AsyncMock())

    await w.start(w._bot)
    try:
        await aio.wait_for(processed.wait(), timeout=2.0)  # recovered after the failure
    finally:
        await w.stop()
    assert picks["n"] >= 2


async def test_loop_continues_when_a_convergence_pass_raises(monkeypatch):
    # _process raising is caught; the batch flushes empty outcomes and the loop lives.
    monkeypatch.setattr(mw.aio, "sleep", AsyncMock())
    w = _worker(monkeypatch)
    picks = {"n": 0}

    async def pick(_bs, **_kw):
        picks["n"] += 1
        return [_row(200)] if picks["n"] == 1 else []

    monkeypatch.setattr(mw.MirrorDelivery, "pick_batch", pick)

    async def process(_batch):
        raise RuntimeError("convergence bug")

    flushed = aio.Event()
    flush_arg: dict[str, object] = {}

    async def flush(outcomes):
        flush_arg["outcomes"] = outcomes
        flushed.set()

    monkeypatch.setattr(w, "_process", process)
    monkeypatch.setattr(w, "_flush", flush)
    monkeypatch.setattr(w, "_evict_resolved_sources", AsyncMock())

    await w.start(w._bot)
    try:
        await aio.wait_for(flushed.wait(), timeout=2.0)
    finally:
        await w.stop()
    assert flush_arg["outcomes"] == []  # process raised → outcomes=[], loop survived


async def test_flush_retries_until_durable(monkeypatch):
    monkeypatch.setattr(mw.aio, "sleep", AsyncMock())
    w = _worker(monkeypatch)
    calls = {"n": 0}

    async def flush_outcomes(_outcomes):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("db down")

    monkeypatch.setattr(mw.MirrorDelivery, "flush_outcomes", flush_outcomes)

    await w._flush(["outcome"])  # keeps retrying until the write lands

    assert calls["n"] == 3


async def test_flush_reraises_cancellation(monkeypatch):
    # A shutdown cancel must propagate (not be retried), so rows recover on restart.
    w = _worker(monkeypatch)

    async def flush_outcomes(_outcomes):
        raise aio.CancelledError

    monkeypatch.setattr(mw.MirrorDelivery, "flush_outcomes", flush_outcomes)

    with pytest.raises(aio.CancelledError):
        await w._flush(["outcome"])


async def test_idle_clears_the_source_cache(monkeypatch):
    w = _worker(monkeypatch)
    w._source_cache = {1: (1, _fake_msg(), {})}

    async def pick(_bs, **_kw):
        return []  # always idle

    monkeypatch.setattr(mw.MirrorDelivery, "pick_batch", pick)

    await w.start(w._bot)
    await aio.sleep(0.05)  # let one idle iteration run
    await w.stop()

    assert w._source_cache == {}


async def test_mixed_group_transient_fetch_backs_off_send_but_deletes(monkeypatch):
    # A group with a send row + a delete row, source fetch fails transiently: the send
    # backs off (it needs the source), the delete still converges (it doesn't).
    _stub_primitives(monkeypatch)
    fetch = AsyncMock(side_effect=ConnectionError("boom"))
    w = _worker(monkeypatch, fetch_message=fetch)

    outcomes = await w._process([_row(10), _row(11, dest_msg_id=100, deleted=True)])

    kinds = _kinds(outcomes)
    assert kinds[10] == OutcomeKind.TRANSIENT  # send backed off behind the bad source
    assert kinds[11] == OutcomeKind.DELETE_SUCCESS  # delete needs no source → converged
