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

The Discord primitives (_send_one / edit_one / _delete_one) and the source fetch are
stubbed; these pin op selection per row shape, one source fetch per group, the
transient/terminal decision (attempt caps, backoff), the PERMANENT→probe→confirmed_dead
gate, cancel short-circuiting, permanent source-fetch handling, and backlog recovery."""

import asyncio
import typing as t
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import hikari as h
import pytest

from dd.beacon import mirror_worker as mw
from dd.beacon.mirror_core import MirrorOperationType, RunView
from dd.beacon.utils import DestVerdict
from dd.common.bot import CachedFetchBot
from dd.common.schemas import ClaimedRow, MirrorDelivery, MirroredChannel, OutcomeKind
from dd.common.utils import ErrorClass

pytestmark = pytest.mark.asyncio


def _row(dest_ch_id, *, dest_msg_id=None, deleted=False, attempts=0, src_msg_id=1):
    return ClaimedRow(
        src_msg_id=src_msg_id,
        dest_ch_id=dest_ch_id,
        src_ch_id=5,
        dest_msg_id=dest_msg_id,
        desired_version=1,
        deleted=deleted,
        attempts=attempts,
    )


def _fake_msg():
    return SimpleNamespace(
        content="hello", embeds=[], id=1, channel_id=5, flags=h.MessageFlag.NONE
    )


def _worker(monkeypatch, *, fetch_message=None):
    """A worker with a stub bot + stubbed role-map DB read; the primitives are stubbed
    per test."""
    fetch = fetch_message or AsyncMock(return_value=_fake_msg())
    bot = SimpleNamespace(rest=SimpleNamespace(fetch_message=fetch))
    monkeypatch.setattr(
        MirroredChannel, "fetch_mirror_and_role_mention_id", AsyncMock(return_value={})
    )
    w = mw.MirrorWorker()
    w._bot = t.cast(CachedFetchBot, bot)
    w._buffer_event = asyncio.Event()
    return w


def _stub_primitives(monkeypatch, *, send=None, edit=None, delete=None):
    monkeypatch.setattr(mw, "_send_one", send or AsyncMock(return_value=7777))
    monkeypatch.setattr(mw, "edit_one", edit or AsyncMock(return_value=0))
    monkeypatch.setattr(mw, "_delete_one", delete or AsyncMock(return_value=None))


def _kinds(w) -> dict[int, OutcomeKind]:
    return {o.dest_ch_id: o.kind for o in w._buffer}


# -- op selection ------------------------------------------------------------


async def test_op_selection_per_row_shape(monkeypatch):
    send, edit, delete = AsyncMock(return_value=7777), AsyncMock(), AsyncMock()
    _stub_primitives(monkeypatch, send=send, edit=edit, delete=delete)
    w = _worker(monkeypatch)
    view = RunView(
        op=MirrorOperationType.UPDATE,
        src_ch_id=5,
        src_msg_id=1,
        total=4,
        start_time=perf_counter(),
    )
    w.register_view(view)
    batch = [
        _row(10),  # no dest, not deleted → send
        _row(11, dest_msg_id=111),  # has dest, not deleted → edit
        _row(12, dest_msg_id=112, deleted=True),  # has dest, deleted → delete
        _row(13, deleted=True),  # deleted, no dest → cancelled (nothing to delete)
    ]
    await w.process(batch)
    send.assert_awaited_once()
    edit.assert_awaited_once()
    delete.assert_awaited_once()
    assert _kinds(w) == {
        10: OutcomeKind.SUCCESS,
        11: OutcomeKind.SUCCESS,
        12: OutcomeKind.DELETE_SUCCESS,
        13: OutcomeKind.CANCELLED,
    }
    # The freshly-sent dest records its new id (invariant 3).
    sent = next(o for o in w._buffer if o.dest_ch_id == 10)
    assert sent.dest_msg_id == 7777
    assert view.delivered == 3  # send + edit + delete converge; 13 cancelled


async def test_one_source_fetch_per_group(monkeypatch):
    fetch = AsyncMock(return_value=_fake_msg())
    _stub_primitives(monkeypatch)
    w = _worker(monkeypatch, fetch_message=fetch)
    await w.process([_row(10), _row(11), _row(12)])  # same src_msg_id
    fetch.assert_awaited_once()


async def test_all_deleted_group_skips_source_fetch(monkeypatch):
    fetch = AsyncMock(return_value=_fake_msg())
    _stub_primitives(monkeypatch)
    w = _worker(monkeypatch, fetch_message=fetch)
    await w.process([_row(10, dest_msg_id=100, deleted=True)])
    fetch.assert_not_awaited()


# -- failure classification --------------------------------------------------


async def test_transient_failure_backs_off(monkeypatch):
    send = AsyncMock(side_effect=TimeoutError("5xx"))
    _stub_primitives(monkeypatch, send=send)
    monkeypatch.setattr(mw, "classify_error", lambda e: ErrorClass.TRANSIENT)
    w = _worker(monkeypatch)
    view = RunView(
        op=MirrorOperationType.SEND,
        src_ch_id=5,
        src_msg_id=1,
        total=1,
        start_time=perf_counter(),
    )
    w.register_view(view)
    await w.process([_row(10)])
    (outcome,) = w._buffer
    assert outcome.kind is OutcomeKind.TRANSIENT
    assert outcome.attempts == 1
    assert outcome.due_at is not None  # scheduled for a later retry
    assert view.retrying == 1


async def test_send_attempt_cap_is_three(monkeypatch):
    _stub_primitives(monkeypatch, send=AsyncMock(side_effect=TimeoutError()))
    monkeypatch.setattr(mw, "classify_error", lambda e: ErrorClass.TRANSIENT)
    w = _worker(monkeypatch)
    # attempts already 2 → this attempt is the 3rd (== send cap) → terminal.
    await w.process([_row(10, attempts=2)])
    assert w._buffer[0].kind is OutcomeKind.TERMINAL


async def test_edit_attempt_cap_is_two(monkeypatch):
    _stub_primitives(monkeypatch, edit=AsyncMock(side_effect=TimeoutError()))
    monkeypatch.setattr(mw, "classify_error", lambda e: ErrorClass.TRANSIENT)
    w = _worker(monkeypatch)
    # An edit (dest_msg_id set) with attempts 1 → 2nd attempt (== edit cap) → terminal.
    await w.process([_row(10, dest_msg_id=100, attempts=1)])
    assert w._buffer[0].kind is OutcomeKind.TERMINAL


async def test_permanent_probes_and_confirms_dead(monkeypatch):
    _stub_primitives(monkeypatch, send=AsyncMock(side_effect=RuntimeError("perm")))
    monkeypatch.setattr(mw, "classify_error", lambda e: ErrorClass.PERMANENT)
    monkeypatch.setattr(mw.cfg, "disable_bad_channels", True)
    monkeypatch.setattr(
        mw.utils,
        "confirm_dest_unsendable",
        AsyncMock(return_value=DestVerdict.CONFIRMED_GONE),
    )
    w = _worker(monkeypatch)
    view = RunView(
        op=MirrorOperationType.SEND,
        src_ch_id=5,
        src_msg_id=1,
        total=1,
        start_time=perf_counter(),
    )
    w.register_view(view)
    await w.process([_row(10)])
    outcome = w._buffer[0]
    assert outcome.kind is OutcomeKind.TERMINAL
    assert outcome.confirmed_dead is True
    assert view.failures[10].confirmed_dead is True


async def test_probe_exception_is_not_confirmed_and_never_raises(monkeypatch):
    _stub_primitives(monkeypatch, send=AsyncMock(side_effect=RuntimeError("perm")))
    monkeypatch.setattr(mw, "classify_error", lambda e: ErrorClass.PERMANENT)
    monkeypatch.setattr(mw.cfg, "disable_bad_channels", True)
    monkeypatch.setattr(
        mw.utils,
        "confirm_dest_unsendable",
        AsyncMock(side_effect=RuntimeError("probe 5xx")),
    )
    w = _worker(monkeypatch)
    await w.process([_row(10)])  # must not raise
    assert w._buffer[0].kind is OutcomeKind.TERMINAL
    assert w._buffer[0].confirmed_dead is False


# -- cancellation & source failure ------------------------------------------


async def test_cancel_requested_short_circuits(monkeypatch):
    send = AsyncMock(return_value=7777)
    _stub_primitives(monkeypatch, send=send)
    w = _worker(monkeypatch)
    view = RunView(
        op=MirrorOperationType.SEND,
        src_ch_id=5,
        src_msg_id=1,
        total=1,
        start_time=perf_counter(),
    )
    view.cancel_requested = True
    w.register_view(view)
    await w.process([_row(10)])
    send.assert_not_awaited()  # never touched Discord
    assert w._buffer[0].kind is OutcomeKind.CANCELLED
    assert view.cancelled_count == 1


async def test_permanent_source_fetch_cancels_group(monkeypatch):
    fetch = AsyncMock(side_effect=RuntimeError("source gone"))
    send = AsyncMock()
    _stub_primitives(monkeypatch, send=send)
    monkeypatch.setattr(mw, "classify_error", lambda e: ErrorClass.PERMANENT)
    w = _worker(monkeypatch, fetch_message=fetch)
    view = RunView(
        op=MirrorOperationType.SEND,
        src_ch_id=5,
        src_msg_id=1,
        total=2,
        start_time=perf_counter(),
    )
    w.register_view(view)
    await w.process([_row(10), _row(11)])
    send.assert_not_awaited()
    assert _kinds(w) == {10: OutcomeKind.CANCELLED, 11: OutcomeKind.CANCELLED}
    assert view.cancelled_count == 2


async def test_transient_source_fetch_backs_off_group(monkeypatch):
    fetch = AsyncMock(side_effect=TimeoutError("5xx"))
    _stub_primitives(monkeypatch)
    monkeypatch.setattr(mw, "classify_error", lambda e: ErrorClass.TRANSIENT)
    w = _worker(monkeypatch, fetch_message=fetch)
    await w.process([_row(10), _row(11)])
    assert _kinds(w) == {10: OutcomeKind.TRANSIENT, 11: OutcomeKind.TRANSIENT}


# -- backlog recovery --------------------------------------------------------


async def test_backlog_recovery_registers_views(monkeypatch):
    backlog = [
        (1000, 5, 3, False, True),  # any_unsent → SEND
        (1001, 6, 2, False, False),  # neither → UPDATE
        (1002, 7, 1, True, False),  # any_deleted → DELETE
    ]
    monkeypatch.setattr(
        MirrorDelivery, "non_terminal_backlog", AsyncMock(return_value=backlog)
    )
    started: list[int] = []

    async def starter(view):
        started.append(view.src_msg_id)

    w = mw.MirrorWorker()
    w._progress_starter = starter
    await w._recover_backlog()
    ops = {smi: w.run_views[smi].op for smi in (1000, 1001, 1002)}
    assert ops == {
        1000: MirrorOperationType.SEND,
        1001: MirrorOperationType.UPDATE,
        1002: MirrorOperationType.DELETE,
    }
    assert w.run_views[1000].total == 3
    assert sorted(started) == [1000, 1001, 1002]
