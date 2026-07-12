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

Exercises the transactional gateway handlers (enqueue/bump/delete/cancel), the pick scan
(ordering, due gate, crosspost pickup), the write-back flusher (every outcome kind incl.
the version/deleted guard and durable crosspost), the count helpers and prune —
on the default SQLite backend."""

import datetime as dt

import pytest
import pytest_asyncio
from sqlalchemy import and_, select, update

from dd.common import schemas
from dd.common.schemas import (
    CrosspostState,
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
            "desired_version": r.desired_version,
            "applied_version": r.applied_version,
            "deleted": bool(r.deleted),
            "state": r.state,
            "crosspost_state": r.crosspost_state,
            "attempts": r.attempts,
            "finished_at": r.finished_at,
            "due_at": r.due_at,
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
    row = await _row(900, 200)
    assert row["state"] == DeliveryState.PENDING.value
    assert row["crosspost_state"] == CrosspostState.NOT_APPLICABLE.value


async def test_enqueue_is_idempotent():
    src = 110
    await MirroredChannel.add_mirror(src, 210, 1, legacy=True)
    assert await MirrorDelivery.enqueue_send(src, 910) == 1
    # A duplicate gateway event / manual re-mirror inserts nothing (INSERT-IGNORE).
    assert await MirrorDelivery.enqueue_send(src, 910) == 0
    assert await _states(910) == {210: DeliveryState.PENDING.value}


async def test_enqueue_non_mirrored_source_inserts_nothing():
    assert await MirrorDelivery.enqueue_send(999, 999_000) == 0
    assert await _states(999_000) == {}


# -- bump_for_edit -----------------------------------------------------------


async def test_bump_for_edit_bumps_version_and_revives_terminal():
    src = 120
    for dest, guild in ((220, 1), (221, 2)):
        await MirroredChannel.add_mirror(src, dest, guild, legacy=True)
    await MirrorDelivery.enqueue_send(src, 920)

    # Drive one to DELIVERED (with a recorded dest message — the delivered baseline that
    # lets an edit reconcile at all), one to FAILED, then edit.
    async with schemas.db_session() as session, session.begin():
        await session.execute(
            update(MirrorDelivery)
            .where(
                and_(MirrorDelivery.src_msg_id == 920, MirrorDelivery.dest_ch_id == 220)
            )
            .values(
                state=DeliveryState.DELIVERED.value, dest_msg_id=7777, applied_version=1
            )
        )
        await session.execute(
            update(MirrorDelivery)
            .where(
                and_(MirrorDelivery.src_msg_id == 920, MirrorDelivery.dest_ch_id == 221)
            )
            .values(state=DeliveryState.FAILED.value)
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
    await MirroredChannel.add_mirror(src, 231, 2, legacy=True)
    await MirrorDelivery.enqueue_send(src, 930)
    # 230 delivered (has a dest message → the reconcile baseline); 231 carries a pending
    # delete-intent and must be left untouched by the edit.
    async with schemas.db_session() as session, session.begin():
        await session.execute(
            update(MirrorDelivery)
            .where(
                and_(MirrorDelivery.src_msg_id == 930, MirrorDelivery.dest_ch_id == 230)
            )
            .values(
                state=DeliveryState.DELIVERED.value, dest_msg_id=6666, applied_version=1
            )
        )
        await session.execute(
            update(MirrorDelivery)
            .where(
                and_(MirrorDelivery.src_msg_id == 930, MirrorDelivery.dest_ch_id == 231)
            )
            .values(deleted=True, desired_version=1)
        )

    # A dest added *after* the original send is picked up by the reconcile insert.
    await MirroredChannel.add_mirror(src, 232, 3, legacy=True)
    bumped, inserted = await MirrorDelivery.bump_for_edit(src, 930)
    # 230 (non-deleted) bumped; 231 (deleted) left untouched; 232 freshly inserted.
    assert bumped == 1
    assert inserted == 1
    assert (await _row(930, 230))["desired_version"] == 2
    assert (await _row(930, 231))["deleted"] is True
    assert (await _row(930, 231))["desired_version"] == 1  # untouched
    assert (await _row(930, 232))["state"] == DeliveryState.PENDING.value


async def test_bump_for_edit_is_a_no_op_before_first_delivery():
    # The publish/crosspost transition Discord reports as a MessageUpdateEvent reaches
    # bump_for_edit with the message enqueued but not delivered anywhere yet (no row has
    # a dest_msg_id). It must be a true no-op: no version bump, no phantom fan-out — the
    # create handler owns the send, and the worker delivers the current content.
    src = 135
    await MirroredChannel.add_mirror(src, 235, 1, legacy=True)
    await MirrorDelivery.enqueue_send(src, 935)

    bumped, inserted = await MirrorDelivery.bump_for_edit(src, 935)
    assert (bumped, inserted) == (0, 0)
    row = await _row(935, 235)
    assert row["desired_version"] == 1  # not bumped
    assert row["state"] == DeliveryState.PENDING.value


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


# -- pick_batch --------------------------------------------------------------


async def test_pick_orders_by_population_then_created_at():
    src = 160
    await MirroredChannel.add_mirror(src, 260, 1, legacy=True)
    await MirroredChannel.add_mirror(src, 261, 2, legacy=True)
    await MirroredChannel.add_mirror(src, 262, 3, legacy=True)  # unknown population
    await ServerStatistics.add_server(1, 5)
    await ServerStatistics.add_server(2, 50)
    await MirrorDelivery.enqueue_send(src, 960)

    now = dt.datetime.now(tz=dt.UTC)
    picked = await MirrorDelivery.pick_batch(10, now=now)
    order = [c.dest_ch_id for c in picked]
    # Unknown population (262) coalesces to the max sentinel → first; then 50, then 5.
    assert order == [262, 261, 260]
    # Picking does not mutate state (no lease).
    assert set((await _states(960)).values()) == {DeliveryState.PENDING.value}


async def test_pick_respects_due_at_gate():
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
    assert await MirrorDelivery.pick_batch(10, now=now) == []
    # Once due, it is picked.
    later = now + dt.timedelta(minutes=11)
    picked = await MirrorDelivery.pick_batch(10, now=later)
    assert [c.dest_ch_id for c in picked] == [270]


async def test_pick_includes_crosspost_pending_but_not_done():
    src = 175
    for dest in (275, 276):
        await MirroredChannel.add_mirror(src, dest, 1, legacy=True)
    await MirrorDelivery.enqueue_send(src, 975)
    now = dt.datetime.now(tz=dt.UTC)
    async with schemas.db_session() as session, session.begin():
        # 275: delivered, crosspost still PENDING → should be picked (to crosspost).
        await session.execute(
            update(MirrorDelivery)
            .where(
                and_(MirrorDelivery.src_msg_id == 975, MirrorDelivery.dest_ch_id == 275)
            )
            .values(
                state=DeliveryState.DELIVERED.value,
                crosspost_state=CrosspostState.PENDING.value,
            )
        )
        # 276: delivered, crosspost DONE → no work left, not picked.
        await session.execute(
            update(MirrorDelivery)
            .where(
                and_(MirrorDelivery.src_msg_id == 975, MirrorDelivery.dest_ch_id == 276)
            )
            .values(
                state=DeliveryState.DELIVERED.value,
                crosspost_state=CrosspostState.DONE.value,
            )
        )
    picked = {c.dest_ch_id: c for c in await MirrorDelivery.pick_batch(10, now=now)}
    assert set(picked) == {275}
    assert picked[275].crosspost_state == CrosspostState.PENDING.value


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


async def test_flush_success_news_marks_crosspost_pending():
    await _seed_one(1005, 605)
    await MirrorDelivery.flush_outcomes(
        [
            DeliveryOutcome(
                kind=OutcomeKind.SUCCESS,
                src_msg_id=1005,
                dest_ch_id=605,
                version=1,
                dest_msg_id=7000,
                crosspost_pending=True,
            )
        ]
    )
    row = await _row(1005, 605)
    assert row["state"] == DeliveryState.DELIVERED.value
    assert row["crosspost_state"] == CrosspostState.PENDING.value


async def test_flush_success_after_edit_bump_returns_pending_but_records_dest_msg():
    # A dest msg id, once observed, is always recorded — even when the version guard
    # fails (edit bumped desired_version mid-flight).
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


async def test_flush_terminal_marks_failed():
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
            )
        ]
    )
    row = await _row(1050, 650)
    assert row["state"] == DeliveryState.FAILED.value
    assert row["last_error_ref"] == "DEAD01"
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


async def test_flush_crosspost_done_and_retry():
    await _seed_one(1080, 680, state=DeliveryState.DELIVERED.value)
    # DONE latches the sub-state terminal.
    await MirrorDelivery.flush_outcomes(
        [DeliveryOutcome(OutcomeKind.CROSSPOST_DONE, 1080, 680, 1)]
    )
    assert (await _row(1080, 680))["crosspost_state"] == CrosspostState.DONE.value

    await _seed_one(1081, 681, state=DeliveryState.DELIVERED.value)
    due = dt.datetime.now(tz=dt.UTC) + dt.timedelta(minutes=4)
    await MirrorDelivery.flush_outcomes(
        [
            DeliveryOutcome(
                OutcomeKind.CROSSPOST_RETRY, 1081, 681, 1, attempts=1, due_at=due
            )
        ]
    )
    row = await _row(1081, 681)
    assert row["crosspost_state"] == CrosspostState.PENDING.value
    assert row["attempts"] == 1


# -- flush guards: a raced edit must not clobber the racing writer's reset ------


async def test_flush_cancelled_latches_for_deleted_never_delivered():
    # A CANCELLED outcome for a delete-intent row that never delivered must latch
    # CANCELLED, not bounce back to PENDING forever (pick→cancel→PENDING livelock).
    await _seed_one(1200, 685, deleted=True, dest_msg_id=None)
    await MirrorDelivery.flush_outcomes(
        [DeliveryOutcome(OutcomeKind.CANCELLED, 1200, 685, 1)]
    )
    assert (await _row(1200, 685))["state"] == DeliveryState.CANCELLED.value


async def test_flush_terminal_after_edit_bump_preserves_reset_attempts():
    # When the version guard fails (edit re-armed the row to attempts=0), a stale
    # TERMINAL outcome must not clobber attempts back to its exhausted count.
    await _seed_one(1210, 690, desired_version=2, attempts=0)
    await MirrorDelivery.flush_outcomes(
        [
            DeliveryOutcome(
                kind=OutcomeKind.TERMINAL,
                src_msg_id=1210,
                dest_ch_id=690,
                version=1,
                attempts=3,
                error_ref="DEAD03",
                error_class="PERMANENT",
            )
        ]
    )
    row = await _row(1210, 690)
    assert row["state"] == DeliveryState.PENDING.value  # keep converging
    assert row["attempts"] == 0  # reset budget preserved, not clobbered to 3


async def test_flush_transient_after_edit_bump_preserves_reset_attempts():
    await _seed_one(1220, 695, desired_version=2, attempts=0)
    far_future = dt.datetime.now(tz=dt.UTC) + dt.timedelta(minutes=5)
    await MirrorDelivery.flush_outcomes(
        [
            DeliveryOutcome(
                kind=OutcomeKind.TRANSIENT,
                src_msg_id=1220,
                dest_ch_id=695,
                version=1,
                attempts=2,
                due_at=far_future,
                error_ref="TMP01",
                error_class="TRANSIENT",
                error_msg="5xx",
            )
        ]
    )
    row = await _row(1220, 695)
    assert row["state"] == DeliveryState.PENDING.value
    assert row["attempts"] == 0  # not bumped to 2


# -- count helpers -----------------------------------------------------------


async def test_non_terminal_counts_only_pending():
    src = 850
    for dest in (730, 731, 732):
        await MirroredChannel.add_mirror(src, dest, 1, legacy=True)
    await MirrorDelivery.enqueue_send(src, 1300)  # 3 PENDING rows
    async with schemas.db_session() as session, session.begin():
        await session.execute(
            update(MirrorDelivery)
            .where(
                and_(
                    MirrorDelivery.src_msg_id == 1300, MirrorDelivery.dest_ch_id == 730
                )
            )
            .values(state=DeliveryState.DELIVERED.value)
        )
        await session.execute(
            update(MirrorDelivery)
            .where(
                and_(
                    MirrorDelivery.src_msg_id == 1300, MirrorDelivery.dest_ch_id == 731
                )
            )
            .values(state=DeliveryState.FAILED.value)
        )
    # 732 PENDING = 1 non-terminal; 730 DELIVERED + 731 FAILED excluded.
    counts = await MirrorDelivery.non_terminal_counts([1300, 999_999])
    assert counts == {1300: 1}  # a source with no non-terminal rows is simply absent


async def test_state_counts_and_outstanding_count():
    src = 855
    for dest in (735, 736, 737):
        await MirroredChannel.add_mirror(src, dest, 1, legacy=True)
    await MirrorDelivery.enqueue_send(src, 1310)
    async with schemas.db_session() as session, session.begin():
        await session.execute(
            update(MirrorDelivery)
            .where(
                and_(
                    MirrorDelivery.src_msg_id == 1310, MirrorDelivery.dest_ch_id == 735
                )
            )
            .values(state=DeliveryState.DELIVERED.value)
        )
    counts = await MirrorDelivery.state_counts(1310)
    assert counts == {
        DeliveryState.DELIVERED.value: 1,
        DeliveryState.PENDING.value: 2,
    }
    assert await MirrorDelivery.outstanding_count() == 2


async def test_failure_breakdown_groups_by_ref():
    src = 858
    for dest in (738, 739, 742):
        await MirroredChannel.add_mirror(src, dest, 1, legacy=True)
    await MirrorDelivery.enqueue_send(src, 1320)
    async with schemas.db_session() as session, session.begin():
        for dest, ref in ((738, "AAA"), (739, "AAA"), (742, "BBB")):
            await session.execute(
                update(MirrorDelivery)
                .where(
                    and_(
                        MirrorDelivery.src_msg_id == 1320,
                        MirrorDelivery.dest_ch_id == dest,
                    )
                )
                .values(
                    state=DeliveryState.FAILED.value,
                    last_error_ref=ref,
                    last_error_class="PERMANENT",
                    last_error_msg="boom",
                )
            )
    breakdown = await MirrorDelivery.failure_breakdown(1320)
    assert [(ref, count) for ref, _cls, count, _msg in breakdown] == [
        ("AAA", 2),
        ("BBB", 1),
    ]


# -- prune -------------------------------------------------------------------


async def test_prune_removes_old_non_delivered():
    for dest in (700, 701):
        await MirroredChannel.add_mirror(800, dest, 1, legacy=True)
    await MirrorDelivery.enqueue_send(800, 1100)
    now = dt.datetime.now(tz=dt.UTC)
    async with schemas.db_session() as session, session.begin():
        # 700: PENDING, 20 days old → pruned (> 14d, non-delivered).
        await session.execute(
            update(MirrorDelivery)
            .where(
                and_(
                    MirrorDelivery.src_msg_id == 1100, MirrorDelivery.dest_ch_id == 700
                )
            )
            .values(created_at=now - dt.timedelta(days=20))
        )
        # 701: FAILED, 20 days old → pruned too (no longer disable evidence).
        await session.execute(
            update(MirrorDelivery)
            .where(
                and_(
                    MirrorDelivery.src_msg_id == 1100, MirrorDelivery.dest_ch_id == 701
                )
            )
            .values(
                created_at=now - dt.timedelta(days=20),
                state=DeliveryState.FAILED.value,
            )
        )
    await MirrorDelivery.prune(now=now)
    assert await _states(1100) == {}


async def test_prune_keeps_latest_delivered_anchor_per_channel():
    # The most-recent DELIVERED per destination channel survives indefinitely; older
    # superseded successes and non-delivered rows past the window are pruned.
    src, dest = 810, 720
    now = dt.datetime.now(tz=dt.UTC)
    async with schemas.db_session() as session, session.begin():
        session.add_all(
            [
                MirrorDelivery(
                    src_msg_id=-9001,
                    dest_ch_id=dest,
                    src_ch_id=src,
                    state=DeliveryState.DELIVERED.value,
                    finished_at=now - dt.timedelta(days=40),
                    created_at=now - dt.timedelta(days=40),
                ),
                MirrorDelivery(
                    src_msg_id=-9002,
                    dest_ch_id=dest,
                    src_ch_id=src,
                    state=DeliveryState.DELIVERED.value,
                    finished_at=now - dt.timedelta(days=30),
                    created_at=now - dt.timedelta(days=30),
                ),
                MirrorDelivery(
                    src_msg_id=-9003,
                    dest_ch_id=dest,
                    src_ch_id=src,
                    state=DeliveryState.FAILED.value,
                    finished_at=now - dt.timedelta(days=35),
                    created_at=now - dt.timedelta(days=35),
                ),
            ]
        )
    await MirrorDelivery.prune(now=now)
    async with schemas.db_session() as session, session.begin():
        rows = (
            await session.execute(
                select(MirrorDelivery.src_msg_id, MirrorDelivery.state).where(
                    MirrorDelivery.dest_ch_id == dest
                )
            )
        ).fetchall()
    kept = {int(smi): st for smi, st in rows}
    assert -9001 not in kept  # superseded success pruned
    assert kept.get(-9002) == DeliveryState.DELIVERED.value  # latest success = anchor
    assert -9003 not in kept  # old FAILED no longer retained


async def test_prune_keeps_all_rows_within_window():
    # Every row inside the 14-day window is kept, including a non-latest delivered one
    # (a channel can hold a second, user-related announcement we may still edit).
    src, dest = 815, 725
    now = dt.datetime.now(tz=dt.UTC)
    async with schemas.db_session() as session, session.begin():
        session.add_all(
            [
                MirrorDelivery(
                    src_msg_id=-8001,
                    dest_ch_id=dest,
                    src_ch_id=src,
                    state=DeliveryState.DELIVERED.value,
                    finished_at=now - dt.timedelta(days=5),
                    created_at=now - dt.timedelta(days=5),
                ),
                MirrorDelivery(
                    src_msg_id=-8002,
                    dest_ch_id=dest,
                    src_ch_id=src,
                    state=DeliveryState.DELIVERED.value,
                    finished_at=now - dt.timedelta(days=3),
                    created_at=now - dt.timedelta(days=3),
                ),
            ]
        )
    await MirrorDelivery.prune(now=now)
    async with schemas.db_session() as session, session.begin():
        rows = (
            await session.execute(
                select(MirrorDelivery.src_msg_id).where(
                    MirrorDelivery.dest_ch_id == dest
                )
            )
        ).fetchall()
    assert {int(r[0]) for r in rows} == {-8001, -8002}


async def test_undo_auto_disable_does_not_poison_empty_cache():
    # Undo re-enabling a pair must not seed an empty srcs cache with only that id (which
    # would make get_or_fetch_all_srcs drop every other legacy source).
    src, dest = 860, 740
    await MirroredChannel.add_mirror(src, dest, 1, legacy=True)
    # Also a second, unrelated legacy source that must remain discoverable.
    await MirroredChannel.add_mirror(870, 741, 1, legacy=True)
    now = dt.datetime.now(tz=dt.UTC)
    async with schemas.db_session() as session, session.begin():
        await session.execute(
            update(MirroredChannel)
            .where(and_(MirroredChannel.src_id == src, MirroredChannel.dest_id == dest))
            .values(enabled=False, legacy_disable_for_failure_on_date=now)
        )
    MirroredChannel._legacy_srcs_cache.clear()  # simulate "no full fetch yet"
    await MirroredChannel.undo_auto_disable_for_failure(
        since=now - dt.timedelta(days=1)
    )
    # The cache was left empty (not poisoned), so a subsequent fetch sees BOTH sources.
    assert await MirroredChannel.get_or_fetch_all_srcs() == {src, 870}
