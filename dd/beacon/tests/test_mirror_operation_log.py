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

"""Integration tests for the mirror operation log (per-op stats).

Covers the append-only store (record / for_message / recent / orphan prune) and the
drain watcher's ``_record_operation`` hook that turns a completed RunView into one
durable operation row — the durable form of the old progress card's per-op numbers.
"""

import datetime as dt
from time import perf_counter

import pytest
import pytest_asyncio

from dd.beacon import mirror_core
from dd.beacon.extensions import mirror
from dd.common import schemas
from dd.common.schemas import (
    DeliveryState,
    MirrorDelivery,
    MirrorOperationLog,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture(autouse=True)
async def _fresh_db():
    await schemas.destroy_all()
    await schemas.create_all()
    yield


def _now() -> dt.datetime:
    return schemas._utcnow()


async def _delivery(src_msg_id: int, **over) -> None:
    now = _now()
    row = dict(
        src_msg_id=src_msg_id,
        dest_ch_id=over.pop("dest_ch_id", 10),
        src_ch_id=1,
        state=DeliveryState.DELIVERED.value,
        created_at=now,
        due_at=now,
    )
    row.update(over)
    async with schemas.db_session() as session, session.begin():
        session.add(MirrorDelivery(**row))


async def test_record_and_for_message_orders_oldest_first() -> None:
    base = _now()
    for i, op in enumerate(("create", "update", "delete")):
        await MirrorOperationLog.record(
            src_msg_id=500,
            src_ch_id=1,
            op_type=op,
            version=i + 1,
            started_at=base + dt.timedelta(minutes=i),
            finished_at=base + dt.timedelta(minutes=i, seconds=30),
            total=150,
            delivered=150,
            failed=0,
            cancelled=0,
            attempts=1,
            failure_refs=None,
        )
    ops = await MirrorOperationLog.for_message(500)
    assert [o["op_type"] for o in ops] == ["create", "update", "delete"]
    assert [o["version"] for o in ops] == [1, 2, 3]
    assert all(isinstance(o["finished_at"], dt.datetime) for o in ops)


async def test_recent_respects_window() -> None:
    now = _now()
    await MirrorOperationLog.record(
        src_msg_id=1,
        src_ch_id=1,
        op_type="create",
        version=1,
        started_at=now,
        finished_at=now,
        total=1,
        delivered=1,
        failed=0,
        cancelled=0,
        attempts=0,
        failure_refs=None,
    )
    await MirrorOperationLog.record(
        src_msg_id=2,
        src_ch_id=1,
        op_type="create",
        version=1,
        started_at=now - dt.timedelta(days=40),
        finished_at=now - dt.timedelta(days=40),
        total=1,
        delivered=1,
        failed=0,
        cancelled=0,
        attempts=0,
        failure_refs=None,
    )
    recent = await MirrorOperationLog.recent(within_days=30)
    assert {o["src_msg_id"] for o in recent} == {1}  # 40-day-old op excluded


async def test_prune_drops_orphaned_ops() -> None:
    await _delivery(700)  # src 700 has a live delivery row; 800 does not
    for src in (700, 800):
        await MirrorOperationLog.record(
            src_msg_id=src,
            src_ch_id=1,
            op_type="create",
            version=1,
            started_at=_now(),
            finished_at=_now(),
            total=1,
            delivered=1,
            failed=0,
            cancelled=0,
            attempts=0,
            failure_refs=None,
        )
    await MirrorOperationLog.prune()
    assert len(await MirrorOperationLog.for_message(700)) == 1  # source lives
    assert await MirrorOperationLog.for_message(800) == []  # orphan pruned


async def test_record_operation_writes_from_runview() -> None:
    # A completed UPDATE run (148 ok, 2 failed) with a bumped version + a failing dest:
    # the drain-watcher hook records the op's own numbers + failure breakdown.
    await _delivery(
        100, dest_ch_id=10, desired_version=2, applied_version=2, attempts=3
    )
    await _delivery(
        100,
        dest_ch_id=11,
        state=DeliveryState.FAILED.value,
        desired_version=2,
        applied_version=1,
        attempts=3,
        last_error_ref="PERM01",
        last_error_class="PERMANENT",
        last_error_msg="Missing Access",
    )
    view = mirror_core.RunView(
        op=mirror_core.MirrorOperationType.UPDATE,
        src_ch_id=1,
        src_msg_id=100,
        start_time=perf_counter() - 5.0,
        counts=mirror_core.RunCounts(delivered=148, failed=2, cancelled=0, pending=0),
    )

    await mirror._record_operation(view)

    (op,) = await MirrorOperationLog.for_message(100)
    assert op["op_type"] == "update"  # SEND->create, UPDATE->update, DELETE->delete
    assert op["version"] == 2  # from the bumped desired_version
    assert (op["delivered"], op["failed"]) == (148, 2)
    assert op["attempts"] == 3
    (fail,) = op["failure_refs"]
    assert fail["ref"] == "PERM01" and fail["count"] == 1
    assert op["finished_at"] >= op["started_at"]
