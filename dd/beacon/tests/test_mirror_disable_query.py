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

"""Integration tests for the reachability-driven auto-disable sweep
(:meth:`MirroredChannel.fetch_reachability_candidates` /
:meth:`MirroredChannel.apply_reachability_sweep`) and its undo.

Pins the properties that replaced the old failure-streak columns: a pair is disabled
only after it stays *continuously* unreachable past the grace window, a reachable probe
resets the clock, an ambiguous (unprobed) pair is never disabled on a given sweep, and
undo re-enables the pair and clears its clock."""

import datetime as dt

import pytest
import pytest_asyncio
from sqlalchemy import and_, select, update

from dd.common import cfg, schemas
from dd.common.schemas import MirroredChannel

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

GRACE = dt.timedelta(hours=cfg.mirror_unreachable_grace_hours)


@pytest_asyncio.fixture(autouse=True)
async def _fresh_db():
    await schemas.destroy_all()
    await schemas.create_all()
    MirroredChannel._legacy_srcs_cache.clear()
    yield


async def _mirror(src_ch, dest_ch, *, legacy=True, enabled=True):
    await MirroredChannel.add_mirror(src_ch, dest_ch, 1, legacy=legacy, enabled=enabled)


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


async def _set_unreachable_since(src, dest, when):
    async with schemas.db_session() as session, session.begin():
        await session.execute(
            update(MirroredChannel)
            .where(and_(MirroredChannel.src_id == src, MirroredChannel.dest_id == dest))
            .values(unreachable_since=when)
        )


async def _unreachable_since(src, dest):
    async with schemas.db_session() as session, session.begin():
        return (
            await session.execute(
                select(MirroredChannel.unreachable_since).where(
                    and_(
                        MirroredChannel.src_id == src,
                        MirroredChannel.dest_id == dest,
                    )
                )
            )
        ).scalar_one()


async def test_fetch_candidates_only_enabled_legacy():
    await _mirror(1, 2)  # enabled legacy → candidate
    await _mirror(3, 4, enabled=False)  # disabled → not a candidate
    await _mirror(5, 6, legacy=False)  # non-legacy → not a candidate
    candidates = set(await MirroredChannel.fetch_reachability_candidates())
    assert candidates == {(1, 2)}


async def test_unreachable_past_grace_disables():
    now1 = dt.datetime.now(tz=dt.UTC)
    await _mirror(1, 2)
    # First sweep stamps the clock but does not disable (not yet past grace).
    assert await MirroredChannel.apply_reachability_sweep([], [(1, 2)], now=now1) == []
    assert (1, 2) in await _enabled_pairs()
    # A later sweep, still unreachable and now past the grace window → disabled.
    now2 = now1 + GRACE + dt.timedelta(hours=1)
    disabled = await MirroredChannel.apply_reachability_sweep([], [(1, 2)], now=now2)
    assert disabled == [(1, 2)]
    assert (1, 2) not in await _enabled_pairs()


async def test_reachable_resets_the_clock():
    now1 = dt.datetime.now(tz=dt.UTC)
    await _mirror(1, 2)
    await MirroredChannel.apply_reachability_sweep([], [(1, 2)], now=now1)
    # It recovers on the next sweep: the clock is cleared.
    now2 = now1 + dt.timedelta(hours=1)
    await MirroredChannel.apply_reachability_sweep([(1, 2)], [], now=now2)
    assert await _unreachable_since(1, 2) is None
    # Even a much later sweep that finds it unreachable only re-stamps (not past grace).
    now3 = now2 + GRACE + dt.timedelta(hours=1)
    assert await MirroredChannel.apply_reachability_sweep([], [(1, 2)], now=now3) == []
    assert (1, 2) in await _enabled_pairs()


async def test_within_grace_not_disabled():
    now1 = dt.datetime.now(tz=dt.UTC)
    await _mirror(1, 2)
    await MirroredChannel.apply_reachability_sweep([], [(1, 2)], now=now1)
    now2 = now1 + GRACE - dt.timedelta(hours=1)  # still inside the window
    assert await MirroredChannel.apply_reachability_sweep([], [(1, 2)], now=now2) == []
    assert (1, 2) in await _enabled_pairs()


async def test_unprobed_pair_not_disabled_even_if_clock_is_old():
    # A pair with an old clock that is NOT confirmed unreachable this sweep (e.g. an
    # UNKNOWN probe) must never be disabled — bias against false disables.
    now = dt.datetime.now(tz=dt.UTC)
    await _mirror(1, 2)  # confirmed unreachable this sweep
    await _mirror(3, 4)  # old clock, but unprobed this sweep
    await _set_unreachable_since(1, 2, now - GRACE - dt.timedelta(hours=1))
    await _set_unreachable_since(3, 4, now - GRACE - dt.timedelta(hours=1))
    disabled = await MirroredChannel.apply_reachability_sweep([], [(1, 2)], now=now)
    assert disabled == [(1, 2)]
    assert (3, 4) in await _enabled_pairs()


async def test_undo_reenables_and_clears_clock():
    now1 = dt.datetime.now(tz=dt.UTC)
    await _mirror(1, 2)
    await MirroredChannel.apply_reachability_sweep([], [(1, 2)], now=now1)
    now2 = now1 + GRACE + dt.timedelta(hours=1)
    assert await MirroredChannel.apply_reachability_sweep([], [(1, 2)], now=now2) == [
        (1, 2)
    ]

    reenabled = await MirroredChannel.undo_auto_disable_for_failure(
        since=now1 - dt.timedelta(days=1)
    )
    assert {tuple(r) for r in reenabled} == {(1, 2)}
    assert (1, 2) in await _enabled_pairs()
    assert await _unreachable_since(1, 2) is None
