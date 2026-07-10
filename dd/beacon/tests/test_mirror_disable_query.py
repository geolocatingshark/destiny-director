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

"""Integration tests for the derived auto-disable streak query
(:meth:`MirroredChannel.disable_failing_mirrors`) and its undo.

Pins the correctness properties the strike columns used to encode by hand: a success
resets the streak, recovery-via-edit resets it, pairs sharing a channel don't
cross-contaminate, the forgiveness window is honoured against the live cfg knob, and
undo neutralises the ledger so the next sweep is a no-op."""

import datetime as dt

import pytest
import pytest_asyncio
from sqlalchemy import and_, select

from dd.common import cfg, schemas
from dd.common.schemas import DeliveryState, MirrorDelivery, MirroredChannel

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

WINDOW = dt.timedelta(hours=cfg.mirror_disable_forgiveness_hours)


@pytest_asyncio.fixture(autouse=True)
async def _fresh_db():
    await schemas.destroy_all()
    await schemas.create_all()
    MirroredChannel._legacy_srcs_cache.clear()
    yield


async def _mirror(src_ch, dest_ch):
    await MirroredChannel.add_mirror(src_ch, dest_ch, 1, legacy=True)


async def _delivery(
    src_ch, dest_ch, src_msg, state, finished_at, *, confirmed_dead=False
):
    async with schemas.db_session() as session, session.begin():
        session.add(
            MirrorDelivery(
                src_msg_id=src_msg,
                dest_ch_id=dest_ch,
                src_ch_id=src_ch,
                desired_version=1,
                applied_version=1,
                deleted=False,
                state=state.value,
                attempts=0,
                confirmed_dead=confirmed_dead,
                finished_at=finished_at,
            )
        )


async def _enabled_pairs() -> set[tuple[int, int]]:
    async with schemas.db_session() as session, session.begin():
        rows = (
            await session.execute(
                select(MirroredChannel.src_id, MirroredChannel.dest_id).where(
                    MirroredChannel.enabled
                )
            )
        ).fetchall()
    return {(int(s), int(d)) for s, d in rows}


def _fail(src_ch, dest_ch, src_msg, finished_at):
    return _delivery(
        src_ch,
        dest_ch,
        src_msg,
        DeliveryState.FAILED,
        finished_at,
        confirmed_dead=True,
    )


async def test_three_old_confirmed_dead_failures_disable():
    now = dt.datetime.now(tz=dt.UTC)
    await _mirror(1, 2)
    for i in range(3):  # 3 distinct source messages, all failing before the window
        await _fail(1, 2, 100 + i, now - WINDOW - dt.timedelta(hours=1))
    disabled = await MirroredChannel.disable_failing_mirrors(now=now)
    assert disabled == [(1, 2)]
    assert (1, 2) not in await _enabled_pairs()


async def test_two_failures_do_not_disable():
    now = dt.datetime.now(tz=dt.UTC)
    await _mirror(1, 2)
    for i in range(2):
        await _fail(1, 2, 100 + i, now - WINDOW - dt.timedelta(hours=1))
    assert await MirroredChannel.disable_failing_mirrors(now=now) == []
    assert (1, 2) in await _enabled_pairs()


async def test_later_success_resets_streak():
    now = dt.datetime.now(tz=dt.UTC)
    await _mirror(1, 2)
    for i in range(3):
        await _fail(1, 2, 100 + i, now - WINDOW - dt.timedelta(hours=2))
    # A success AFTER the failures resets the streak → no disable.
    await _delivery(
        1, 2, 200, DeliveryState.DELIVERED, now - WINDOW - dt.timedelta(hours=1)
    )
    assert await MirroredChannel.disable_failing_mirrors(now=now) == []
    assert (1, 2) in await _enabled_pairs()


async def test_earlier_success_does_not_save():
    now = dt.datetime.now(tz=dt.UTC)
    await _mirror(1, 2)
    # Success first, THEN three failures after it → streak stands.
    await _delivery(1, 2, 50, DeliveryState.DELIVERED, now - dt.timedelta(days=10))
    for i in range(3):
        await _fail(1, 2, 100 + i, now - dt.timedelta(days=5))
    assert await MirroredChannel.disable_failing_mirrors(now=now) == [(1, 2)]


async def test_recovery_via_edit_resets():
    now = dt.datetime.now(tz=dt.UTC)
    await _mirror(1, 2)
    # Two rows stay FAILED, one previously-failed row was re-delivered by an edit
    # (flipped to DELIVERED with the newest finished_at).
    await _fail(1, 2, 100, now - dt.timedelta(days=5))
    await _fail(1, 2, 101, now - dt.timedelta(days=5))
    await _delivery(1, 2, 102, DeliveryState.DELIVERED, now - dt.timedelta(days=1))
    assert await MirroredChannel.disable_failing_mirrors(now=now) == []


async def test_per_pair_granularity_no_cross_contamination():
    now = dt.datetime.now(tz=dt.UTC)
    # Pair (1,2) genuinely failing; pair (3,2) shares dest 2 but is healthy.
    await _mirror(1, 2)
    await _mirror(3, 2)
    for i in range(3):
        await _fail(1, 2, 100 + i, now - WINDOW - dt.timedelta(hours=1))
    disabled = await MirroredChannel.disable_failing_mirrors(now=now)
    assert disabled == [(1, 2)]
    assert (3, 2) in await _enabled_pairs()


async def test_forgiveness_window_boundary():
    now = dt.datetime.now(tz=dt.UTC)
    await _mirror(1, 2)  # oldest failure just OLDER than the window → dead
    await _mirror(3, 4)  # oldest failure just YOUNGER than the window → survives
    for i in range(3):
        await _fail(1, 2, 100 + i, now - WINDOW - dt.timedelta(hours=1))
    for i in range(3):
        await _fail(3, 4, 200 + i, now - WINDOW + dt.timedelta(hours=1))
    disabled = await MirroredChannel.disable_failing_mirrors(now=now)
    assert disabled == [(1, 2)]
    enabled = await _enabled_pairs()
    assert (1, 2) not in enabled
    assert (3, 4) in enabled


async def test_undo_reenables_and_neutralises_ledger():
    now = dt.datetime.now(tz=dt.UTC)
    await _mirror(1, 2)
    for i in range(3):
        await _fail(1, 2, 100 + i, now - WINDOW - dt.timedelta(hours=1))
    assert await MirroredChannel.disable_failing_mirrors(now=now) == [(1, 2)]

    reenabled = await MirroredChannel.undo_auto_disable_for_failure(
        since=now - dt.timedelta(days=1)
    )
    assert {tuple(r) for r in reenabled} == {(1, 2)}
    assert (1, 2) in await _enabled_pairs()

    # The FAILED rows are now CANCELLED, so the next sweep is a no-op (no immediate
    # re-disable of the just-undone pair).
    async with schemas.db_session() as session, session.begin():
        remaining_failed = (
            await session.execute(
                select(MirrorDelivery.dest_ch_id).where(
                    and_(
                        MirrorDelivery.src_ch_id == 1,
                        MirrorDelivery.state == DeliveryState.FAILED.value,
                    )
                )
            )
        ).fetchall()
    assert remaining_failed == []
    assert await MirroredChannel.disable_failing_mirrors(now=now) == []
