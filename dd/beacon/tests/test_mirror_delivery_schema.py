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

"""Integration tests for the durable delivery ledger (:class:`MirrorDelivery`).

Exercises the transactional gateway handlers (enqueue/bump/delete/cancel), the claim
scan (ordering, gating, stale reclaim), the write-back flusher (every outcome kind incl.
the version/deleted guard) and prune retention — on the default SQLite backend."""

import datetime as dt

import pytest
import pytest_asyncio
from sqlalchemy import and_, select, update

from dd.common import schemas
from dd.common.schemas import (
    ClaimedRow,
    DeliveryOutcome,
    DeliveryState,
    MirrorDelivery,
    MirroredChannel,
    OutcomeKind,
    ServerStatistics,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture(autouse=True)
async def _fresh_db():
    await schemas.destroy_all()
    await schemas.create_all()
    MirroredChannel._legacy_srcs_cache.clear()
    yield


async def _row(src_msg_id: int, dest_ch_id: int) -> dict:
    async with schemas.db_session() as session, session.begin():
        r = (
            await session.execute(
                select(MirrorDelivery).where(
                    and_(
                        MirrorDelivery.src_msg_id == src_msg_id,
                        MirrorDelivery.dest_ch_id == dest_ch_id,
                    )
                )
            )
        ).scalar_one_or_none()
        assert r is not None, f"no delivery row for ({src_msg_id}, {dest_ch_id})"
        return {
            "dest_msg_id": r.dest_msg_id,
            "src_ch_id": r.src_ch_id,
            "dest_server_id": r.dest_server_id,
            "desired_version": r.desired_version,
            "applied_version": r.applied_version,
            "deleted": bool(r.deleted),
            "state": r.state,
            "attempts": r.attempts,
            "confirmed_dead": bool(r.confirmed_dead),
            "finished_at": r.finished_at,
            "last_error_ref": r.last_error_ref,
        }


async def _states(src_msg_id: int) -> dict[int, str]:
    async with schemas.db_session() as session, session.begin():
        rows = (
            await session.execute(
                select(MirrorDelivery.dest_ch_id, MirrorDelivery.state).where(
                    MirrorDelivery.src_msg_id == src_msg_id
                )
            )
        ).fetchall()
    return {int(d): s for d, s in rows}


# -- enqueue -----------------------------------------------------------------


async def test_enqueue_inserts_only_enabled_legacy_dests_minus_source():
    src = 100
    await MirroredChannel.add_mirror(src, 200, 1, legacy=True)
    await MirroredChannel.add_mirror(src, 201, 2, legacy=True)
    await MirroredChannel.add_mirror(src, 202, 3, legacy=True, enabled=False)
    await MirroredChannel.add_mirror(src, 203, 4, legacy=False)  # channel-follow
    await MirroredChannel.add_mirror(src, src, 5, legacy=True)  # self → excluded

    inserted = await MirrorDelivery.enqueue_send(src, 900)
    assert inserted == 2
    assert set(await _states(900)) == {200, 201}
    # dest_server_id was denormalised from the mirror row for claim ordering.
    assert (await _row(900, 200))["dest_server_id"] == 1


async def test_enqueue_is_idempotent():
    src = 110
    await MirroredChannel.add_mirror(src, 210, 1, legacy=True)
    assert await MirrorDelivery.enqueue_send(src, 910) == 1
    # A duplicate gateway event / manual re-mirror inserts nothing (INSERT-IGNORE).
    assert await MirrorDelivery.enqueue_send(src, 910) == 0
    states = await _states(910)
    assert states == {210: DeliveryState.PENDING.value}


async def test_enqueue_non_mirrored_source_inserts_nothing():
    assert await MirrorDelivery.enqueue_send(999, 999_000) == 0
    assert await _states(999_000) == {}


# -- bump_for_edit -----------------------------------------------------------


async def test_bump_for_edit_bumps_version_and_revives_terminal():
    src = 120
    for dest, guild in ((220, 1), (221, 2)):
        await MirroredChannel.add_mirror(src, dest, guild, legacy=True)
    await MirrorDelivery.enqueue_send(src, 920)

    # Drive one to DELIVERED, one to FAILED, then edit.
    async with schemas.db_session() as session, session.begin():
        await session.execute(
            update(MirrorDelivery)
            .where(
                and_(MirrorDelivery.src_msg_id == 920, MirrorDelivery.dest_ch_id == 220)
            )
            .values(state=DeliveryState.DELIVERED.value, applied_version=1)
        )
        await session.execute(
            update(MirrorDelivery)
            .where(
                and_(MirrorDelivery.src_msg_id == 920, MirrorDelivery.dest_ch_id == 221)
            )
            .values(state=DeliveryState.FAILED.value, confirmed_dead=True)
        )

    bumped, inserted = await MirrorDelivery.bump_for_edit(src, 920)
    assert (bumped, inserted) == (2, 0)
    for dest in (220, 221):
        row = await _row(920, dest)
        assert row["state"] == DeliveryState.PENDING.value
        assert row["desired_version"] == 2  # bumped past applied_version=1/0
        assert row["attempts"] == 0


async def test_bump_for_edit_leaves_deleted_rows_untouched_and_inserts_new_dest():
    src = 130
    await MirroredChannel.add_mirror(src, 230, 1, legacy=True)
    await MirrorDelivery.enqueue_send(src, 930)
    await MirrorDelivery.mark_deleted(
        930
    )  # 230 → PENDING deleted (no dest yet → CANCELLED)

    # A dest added *after* the original send is picked up by the reconcile insert.
    await MirroredChannel.add_mirror(src, 231, 2, legacy=True)
    bumped, inserted = await MirrorDelivery.bump_for_edit(src, 930)
    # 230 is deleted → not bumped; 231 is freshly inserted.
    assert bumped == 0
    assert inserted == 1
    assert (await _row(930, 230))["deleted"] is True
    assert (await _row(930, 231))["state"] == DeliveryState.PENDING.value


# -- mark_deleted ------------------------------------------------------------


async def test_mark_deleted_case_semantics():
    src = 140
    for dest, guild in ((240, 1), (241, 2)):
        await MirroredChannel.add_mirror(src, dest, guild, legacy=True)
    await MirrorDelivery.enqueue_send(src, 940)
    # 240 already delivered (has a dest msg), 241 never delivered.
    async with schemas.db_session() as session, session.begin():
        await session.execute(
            update(MirrorDelivery)
            .where(
                and_(MirrorDelivery.src_msg_id == 940, MirrorDelivery.dest_ch_id == 240)
            )
            .values(
                state=DeliveryState.DELIVERED.value, dest_msg_id=5555, applied_version=1
            )
        )

    deletion_work = await MirrorDelivery.mark_deleted(940)
    assert deletion_work == 1  # only 240 (delivered) needs a Discord delete
    # delivered → PENDING with delete-intent; never-delivered → CANCELLED.
    assert (await _row(940, 240))["state"] == DeliveryState.PENDING.value
    assert (await _row(940, 240))["deleted"] is True
    assert (await _row(940, 241))["state"] == DeliveryState.CANCELLED.value


# -- cancel_pending ----------------------------------------------------------


async def test_cancel_pending_only_pending_undeleted():
    src = 150
    for dest in (250, 251):
        await MirroredChannel.add_mirror(src, dest, 1, legacy=True)
    await MirrorDelivery.enqueue_send(src, 950)
    # 251 delivered already → not cancellable.
    async with schemas.db_session() as session, session.begin():
        await session.execute(
            update(MirrorDelivery)
            .where(
                and_(MirrorDelivery.src_msg_id == 950, MirrorDelivery.dest_ch_id == 251)
            )
            .values(state=DeliveryState.DELIVERED.value)
        )
    cancelled = await MirrorDelivery.cancel_pending(950)
    assert cancelled == [250]
    assert (await _row(950, 250))["state"] == DeliveryState.CANCELLED.value
    assert (await _row(950, 251))["state"] == DeliveryState.DELIVERED.value


# -- claim_batch -------------------------------------------------------------


async def _enqueue_with_pop(src, src_msg, dest, guild, pop):
    await MirroredChannel.add_mirror(src, dest, guild, legacy=True)
    await ServerStatistics.add_server(guild, pop) if pop is not None else None


async def test_claim_orders_by_population_then_created_at():
    src = 160
    await MirroredChannel.add_mirror(src, 260, 1, legacy=True)
    await MirroredChannel.add_mirror(src, 261, 2, legacy=True)
    await MirroredChannel.add_mirror(src, 262, 3, legacy=True)  # unknown population
    await ServerStatistics.add_server(1, 5)
    await ServerStatistics.add_server(2, 50)
    await MirrorDelivery.enqueue_send(src, 960)

    now = dt.datetime.now(tz=dt.UTC)
    stale = now - dt.timedelta(hours=1)
    claimed = await MirrorDelivery.claim_batch("w1", 10, stale, now=now)
    order = [c.dest_ch_id for c in claimed]
    # Unknown population (262) coalesces to the max sentinel → first; then 50, then 5.
    assert order == [262, 261, 260]
    assert all(isinstance(c, ClaimedRow) for c in claimed)
    # All are now CLAIMED.
    assert set((await _states(960)).values()) == {DeliveryState.CLAIMED.value}


async def test_claim_respects_due_at_gate():
    src = 170
    await MirroredChannel.add_mirror(src, 270, 1, legacy=True)
    await MirrorDelivery.enqueue_send(src, 970)
    now = dt.datetime.now(tz=dt.UTC)
    # Push due_at into the future.
    async with schemas.db_session() as session, session.begin():
        await session.execute(
            update(MirrorDelivery)
            .where(MirrorDelivery.src_msg_id == 970)
            .values(due_at=now + dt.timedelta(minutes=10))
        )
    assert await MirrorDelivery.claim_batch("w1", 10, now, now=now) == []
    # Once due, it claims.
    later = now + dt.timedelta(minutes=11)
    claimed = await MirrorDelivery.claim_batch("w1", 10, later, now=later)
    assert [c.dest_ch_id for c in claimed] == [270]


async def test_claim_reclaims_stale():
    src = 180
    await MirroredChannel.add_mirror(src, 280, 1, legacy=True)
    await MirrorDelivery.enqueue_send(src, 980)
    now = dt.datetime.now(tz=dt.UTC)
    # Mark it CLAIMED with an old claimed_at.
    async with schemas.db_session() as session, session.begin():
        await session.execute(
            update(MirrorDelivery)
            .where(MirrorDelivery.src_msg_id == 980)
            .values(
                state=DeliveryState.CLAIMED.value,
                claimed_by="dead",
                claimed_at=now - dt.timedelta(hours=1),
            )
        )
    # A stale_cutoff newer than claimed_at reclaims it.
    claimed = await MirrorDelivery.claim_batch(
        "w2", 10, now - dt.timedelta(minutes=30), now=now
    )
    assert [c.dest_ch_id for c in claimed] == [280]
    assert (await _row(980, 280))["state"] == DeliveryState.CLAIMED.value


# -- flush_outcomes ----------------------------------------------------------


async def _seed_one(src_msg, dest_ch, **overrides):
    await MirroredChannel.add_mirror(500, dest_ch, 1, legacy=True)
    await MirrorDelivery.enqueue_send(500, src_msg)
    if overrides:
        async with schemas.db_session() as session, session.begin():
            await session.execute(
                update(MirrorDelivery)
                .where(
                    and_(
                        MirrorDelivery.src_msg_id == src_msg,
                        MirrorDelivery.dest_ch_id == dest_ch,
                    )
                )
                .values(**overrides)
            )


async def test_flush_success_delivers_and_records_dest_msg():
    await _seed_one(1000, 600)
    await MirrorDelivery.flush_outcomes(
        [
            DeliveryOutcome(
                kind=OutcomeKind.SUCCESS,
                src_msg_id=1000,
                dest_ch_id=600,
                version=1,
                dest_msg_id=7777,
            )
        ]
    )
    row = await _row(1000, 600)
    assert row["state"] == DeliveryState.DELIVERED.value
    assert row["dest_msg_id"] == 7777
    assert row["applied_version"] == 1
    assert row["finished_at"] is not None


async def test_flush_success_after_edit_bump_returns_pending_but_records_dest_msg():
    # Invariant 3: a dest msg id, once observed, is always recorded — even when the
    # version guard fails (edit bumped desired_version mid-flight).
    await _seed_one(1010, 610, desired_version=2)
    await MirrorDelivery.flush_outcomes(
        [
            DeliveryOutcome(
                kind=OutcomeKind.SUCCESS,
                src_msg_id=1010,
                dest_ch_id=610,
                version=1,
                dest_msg_id=8888,
            )
        ]
    )
    row = await _row(1010, 610)
    assert row["state"] == DeliveryState.PENDING.value  # re-converge (edit)
    assert row["dest_msg_id"] == 8888  # still recorded → re-convergence edits
    assert row["finished_at"] is None


async def test_flush_success_after_delete_returns_pending():
    await _seed_one(1020, 620, deleted=True, dest_msg_id=None)
    await MirrorDelivery.flush_outcomes(
        [
            DeliveryOutcome(
                kind=OutcomeKind.SUCCESS,
                src_msg_id=1020,
                dest_ch_id=620,
                version=1,
                dest_msg_id=9999,
            )
        ]
    )
    row = await _row(1020, 620)
    assert row["state"] == DeliveryState.PENDING.value  # delete raced the send
    assert row["dest_msg_id"] == 9999  # recorded → worker now deletes it


async def test_flush_delete_success_is_terminal():
    await _seed_one(1030, 630, deleted=True, dest_msg_id=1234)
    await MirrorDelivery.flush_outcomes(
        [
            DeliveryOutcome(
                kind=OutcomeKind.DELETE_SUCCESS,
                src_msg_id=1030,
                dest_ch_id=630,
                version=1,
            )
        ]
    )
    row = await _row(1030, 630)
    assert row["state"] == DeliveryState.DELIVERED.value
    assert row["finished_at"] is not None


async def test_flush_transient_backs_off():
    await _seed_one(1040, 640)
    due = dt.datetime.now(tz=dt.UTC) + dt.timedelta(minutes=4)
    await MirrorDelivery.flush_outcomes(
        [
            DeliveryOutcome(
                kind=OutcomeKind.TRANSIENT,
                src_msg_id=1040,
                dest_ch_id=640,
                version=1,
                attempts=1,
                due_at=due,
                error_ref="ABC123",
                error_class="TRANSIENT",
                error_msg="5xx",
            )
        ]
    )
    row = await _row(1040, 640)
    assert row["state"] == DeliveryState.PENDING.value
    assert row["attempts"] == 1
    assert row["last_error_ref"] == "ABC123"


async def test_flush_terminal_marks_failed_and_records_confirmed_dead():
    await _seed_one(1050, 650)
    await MirrorDelivery.flush_outcomes(
        [
            DeliveryOutcome(
                kind=OutcomeKind.TERMINAL,
                src_msg_id=1050,
                dest_ch_id=650,
                version=1,
                attempts=3,
                error_ref="DEAD01",
                error_class="PERMANENT",
                error_msg="forbidden",
                confirmed_dead=True,
            )
        ]
    )
    row = await _row(1050, 650)
    assert row["state"] == DeliveryState.FAILED.value
    assert row["confirmed_dead"] is True
    assert row["finished_at"] is not None


async def test_flush_terminal_after_edit_bump_keeps_converging():
    await _seed_one(1060, 660, desired_version=2)
    await MirrorDelivery.flush_outcomes(
        [
            DeliveryOutcome(
                kind=OutcomeKind.TERMINAL,
                src_msg_id=1060,
                dest_ch_id=660,
                version=1,
                attempts=3,
                error_ref="DEAD02",
                error_class="PERMANENT",
                confirmed_dead=True,
            )
        ]
    )
    row = await _row(1060, 660)
    assert row["state"] == DeliveryState.PENDING.value  # edit raced → keep converging


async def test_flush_cancelled_guarded():
    await _seed_one(1070, 670)
    await MirrorDelivery.flush_outcomes(
        [
            DeliveryOutcome(
                kind=OutcomeKind.CANCELLED,
                src_msg_id=1070,
                dest_ch_id=670,
                version=1,
            )
        ]
    )
    assert (await _row(1070, 670))["state"] == DeliveryState.CANCELLED.value


# -- prune -------------------------------------------------------------------


async def test_prune_retention_21d_and_90d():
    for dest in (700, 701, 702):
        await MirroredChannel.add_mirror(800, dest, 1, legacy=True)
    await MirrorDelivery.enqueue_send(800, 1100)
    now = dt.datetime.now(tz=dt.UTC)
    async with schemas.db_session() as session, session.begin():
        # 700: PENDING, 30 days old → pruned at 21d.
        await session.execute(
            update(MirrorDelivery)
            .where(
                and_(
                    MirrorDelivery.src_msg_id == 1100, MirrorDelivery.dest_ch_id == 700
                )
            )
            .values(created_at=now - dt.timedelta(days=30))
        )
        # 701: FAILED, 30 days old → kept (terminal evidence), pruned only at 90d.
        await session.execute(
            update(MirrorDelivery)
            .where(
                and_(
                    MirrorDelivery.src_msg_id == 1100, MirrorDelivery.dest_ch_id == 701
                )
            )
            .values(
                created_at=now - dt.timedelta(days=30),
                state=DeliveryState.FAILED.value,
            )
        )
        # 702: FAILED, 100 days old → pruned at 90d.
        await session.execute(
            update(MirrorDelivery)
            .where(
                and_(
                    MirrorDelivery.src_msg_id == 1100, MirrorDelivery.dest_ch_id == 702
                )
            )
            .values(
                created_at=now - dt.timedelta(days=100),
                state=DeliveryState.FAILED.value,
            )
        )
    await MirrorDelivery.prune(now=now)
    states = await _states(1100)
    assert 700 not in states  # non-terminal 30d → gone
    assert states.get(701) == DeliveryState.FAILED.value  # FAILED 30d → kept
    assert 702 not in states  # FAILED 100d → gone
