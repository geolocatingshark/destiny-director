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

"""Unit tests for the write-back flusher (:meth:`MirrorWorker._flush_once`).

Pins the owner's continuous-flusher design: outcomes accrued *during* a write survive
to the next cycle (swap semantics), a DB error re-queues the batch at the front and
retries, and the flusher never touches Discord (it runs with no bot wired)."""

import asyncio

import pytest

from dd.beacon import mirror_worker as mw
from dd.common.schemas import DeliveryOutcome, MirrorDelivery, OutcomeKind

pytestmark = pytest.mark.asyncio


def _outcome(dest: int) -> DeliveryOutcome:
    return DeliveryOutcome(
        kind=OutcomeKind.SUCCESS,
        src_msg_id=1,
        dest_ch_id=dest,
        version=1,
        dest_msg_id=dest,
    )


def _worker() -> mw.MirrorWorker:
    w = mw.MirrorWorker()
    w._buffer_event = asyncio.Event()
    w._bot = None  # the flusher must never need a bot
    return w


async def test_flush_writes_buffer_and_clears_it(monkeypatch):
    seen: list[list[DeliveryOutcome]] = []

    async def fake_flush(outcomes, **_):
        seen.append(list(outcomes))

    monkeypatch.setattr(MirrorDelivery, "flush_outcomes", fake_flush)
    w = _worker()
    w._buffer = [_outcome(10), _outcome(11)]
    assert await w._flush_once() is True
    assert [o.dest_ch_id for o in seen[0]] == [10, 11]
    assert w._buffer == []


async def test_outcomes_accrued_during_write_survive(monkeypatch):
    calls: list[list[int]] = []

    async def fake_flush(outcomes, **_):
        # Simulate a delivery coroutine appending a new outcome mid-write.
        calls.append([o.dest_ch_id for o in outcomes])
        if len(calls) == 1:
            w._buffer.append(_outcome(20))

    monkeypatch.setattr(MirrorDelivery, "flush_outcomes", fake_flush)
    w = _worker()
    w._buffer = [_outcome(10)]
    assert await w._flush_once() is True  # writes [10], but 20 accrued during the swap
    assert w._buffer and w._buffer[0].dest_ch_id == 20
    assert await w._flush_once() is True  # next cycle writes [20]
    assert calls == [[10], [20]]


async def test_db_error_requeues_at_front_and_retries(monkeypatch):
    attempts = {"n": 0}

    async def flaky_flush(outcomes, **_):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("db down")

    monkeypatch.setattr(MirrorDelivery, "flush_outcomes", flaky_flush)
    w = _worker()
    w._buffer = [_outcome(10), _outcome(11)]
    assert await w._flush_once() is False  # first attempt fails
    assert [o.dest_ch_id for o in w._buffer] == [10, 11]  # re-queued, order preserved
    assert await w._flush_once() is True  # retry succeeds
    assert w._buffer == []


async def test_empty_buffer_is_a_noop(monkeypatch):
    called = False

    async def fake_flush(outcomes, **_):
        nonlocal called
        called = True

    monkeypatch.setattr(MirrorDelivery, "flush_outcomes", fake_flush)
    w = _worker()
    assert await w._flush_once() is True
    assert called is False
