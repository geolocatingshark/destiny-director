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

import datetime as dt

import pytest
import pytest_asyncio
from sqlalchemy import select, tuple_, update

from dd.common import schemas
from dd.common.schemas import (
    MirroredChannel as _MirroredChannel,
    ServerStatistics,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(autouse=True)
async def _fresh_db():
    await schemas.destroy_all()
    await schemas.create_all()
    yield


@pytest.fixture()
def MirroredChannel():
    # Clear the cache before each test
    _MirroredChannel._legacy_srcs_cache.clear()
    yield _MirroredChannel


async def assert_all_srcs_equals(
    src_list: list[int] | set[int], mirrored_channel: _MirroredChannel
):
    src_list = set(src_list)
    assert src_list == await mirrored_channel.fetch_all_srcs()
    assert src_list == await mirrored_channel.fetch_all_srcs(legacy=True)
    assert src_list == await mirrored_channel.get_or_fetch_all_srcs()
    assert src_list == await mirrored_channel.get_or_fetch_all_srcs(legacy=True)


@pytest.mark.asyncio
async def test_add_and_fetch_mirror(MirroredChannel):
    src_id = 0
    dest_id = 1
    dest_id_2 = 2
    guild_id = 3

    await MirroredChannel.add_mirror(src_id, dest_id, guild_id, legacy=True)

    async with schemas.db_session() as session, session.begin():
        assert [src_id] == await MirroredChannel.fetch_srcs(
            dest_id, legacy=None, session=session
        )
        assert [src_id] == await MirroredChannel.fetch_srcs(dest_id, session=session)
        assert (
            await MirroredChannel.fetch_srcs(dest_id, legacy=False, session=session)
            == []
        )
        assert [dest_id] == await MirroredChannel.fetch_dests(
            src_id, legacy=None, session=session
        )
        assert [dest_id] == await MirroredChannel.fetch_dests(src_id, session=session)
        assert (
            await MirroredChannel.fetch_dests(src_id, legacy=False, session=session)
            == []
        )

        await MirroredChannel.add_mirror(
            src_id, dest_id_2, guild_id, legacy=False, session=session
        )

        assert [src_id] == await MirroredChannel.fetch_srcs(
            dest_id_2, legacy=False, session=session
        )
        assert await MirroredChannel.fetch_srcs(dest_id_2, session=session) == []
        assert [src_id] == await MirroredChannel.fetch_srcs(
            dest_id_2, legacy=None, session=session
        )
        assert [dest_id, dest_id_2] == await MirroredChannel.fetch_dests(
            src_id, legacy=None, session=session
        )
        assert [dest_id] == await MirroredChannel.fetch_dests(src_id, session=session)
        assert [dest_id_2] == await MirroredChannel.fetch_dests(
            src_id, legacy=False, session=session
        )


@pytest.mark.asyncio
async def test_remove_mirror(MirroredChannel):
    src_id = 0
    dest_id = 1
    dest_id_2 = 2
    guild_id = 3

    await MirroredChannel.add_mirror(src_id, dest_id, guild_id, legacy=True)
    await MirroredChannel.add_mirror(src_id, dest_id_2, guild_id, legacy=True)
    assert [src_id] == await MirroredChannel.fetch_srcs(dest_id)
    assert [src_id] == await MirroredChannel.fetch_srcs(dest_id_2)
    assert [dest_id, dest_id_2] == await MirroredChannel.fetch_dests(src_id)

    await MirroredChannel.remove_mirror(src_id, dest_id)
    assert dest_id not in await MirroredChannel.fetch_dests(src_id)


@pytest.mark.asyncio
async def test_remove_all_mirrors(MirroredChannel):
    src_id = 0
    src_id_2 = 1
    dest_id = 2
    guild_id = 3

    await MirroredChannel.add_mirror(src_id, dest_id, guild_id, legacy=True)
    await MirroredChannel.add_mirror(src_id_2, dest_id, guild_id, legacy=True)
    assert [dest_id] == await MirroredChannel.fetch_dests(src_id)
    assert [dest_id] == await MirroredChannel.fetch_dests(src_id_2)
    assert [src_id, src_id_2] == await MirroredChannel.fetch_srcs(dest_id)

    await MirroredChannel.remove_all_mirrors(dest_id)
    assert await MirroredChannel.fetch_srcs(dest_id) == []


@pytest.mark.asyncio
async def test_add_duplicate_mirror(MirroredChannel):
    # Note, this should not raise an error since
    # add_mirror uses merge instead of add
    src_id = 0
    dest_id = 1
    guild_id = 2

    await MirroredChannel.add_mirror(src_id, dest_id, guild_id, legacy=True)
    assert [dest_id] == await MirroredChannel.fetch_dests(src_id)
    assert [src_id] == await MirroredChannel.fetch_srcs(dest_id)

    # Errors here indicate that there was an issue merging
    await MirroredChannel.add_mirror(src_id, dest_id, guild_id, legacy=True)

    # Duplicates here should not show up
    assert [dest_id] == await MirroredChannel.fetch_dests(src_id)
    assert [src_id] == await MirroredChannel.fetch_srcs(dest_id)


@pytest.mark.asyncio
async def test_count_dests(MirroredChannel):
    src_id = 0
    src_id_2 = 1
    dest_id = 2
    dest_id_2 = 3
    guild_id = 4

    assert await MirroredChannel.count_dests(src_id) == 0
    assert await MirroredChannel.count_dests(src_id_2) == 0
    await MirroredChannel.add_mirror(src_id, dest_id, guild_id, legacy=True)
    await MirroredChannel.add_mirror(src_id_2, dest_id, guild_id, legacy=True)
    assert await MirroredChannel.count_dests(src_id) == 1
    assert await MirroredChannel.count_dests(src_id_2) == 1
    assert await MirroredChannel.count_dests(dest_id) == 0
    await MirroredChannel.add_mirror(src_id, dest_id_2, guild_id, legacy=True)
    assert await MirroredChannel.count_dests(src_id) == 2
    assert await MirroredChannel.count_dests(src_id_2) == 1
    assert await MirroredChannel.count_dests(dest_id) == 0
    assert await MirroredChannel.count_dests(dest_id_2) == 0


@pytest.mark.asyncio
async def test_order_fetch_by_server_size(MirroredChannel: _MirroredChannel):
    src_id = 0

    dest_id_1 = 1
    guild_id_1 = 1
    low_pop = 1 * 10**6

    dest_id_2 = 2
    guild_id_2 = 2
    medium_pop = 2 * 10**6

    dest_id_3 = 3
    guild_id_3 = 3
    high_pop = 3 * 10**6

    async def add_mirror(src_id, dest_id, guild_id, pop=None):
        await MirroredChannel.add_mirror(src_id, dest_id, guild_id, legacy=True)
        if pop:
            await ServerStatistics.add_server(guild_id, pop)
        else:
            await ServerStatistics.add_server(guild_id)

    await add_mirror(src_id, dest_id_1, guild_id_1, low_pop)
    await add_mirror(src_id, dest_id_2, guild_id_2, medium_pop)
    # Ensure the default value for guild_id_3 is the largest
    await add_mirror(src_id, dest_id_3, guild_id_3)

    dests_in_order = await MirroredChannel.fetch_dests(src_id)
    assert dests_in_order == [
        dest_id_3,
        dest_id_2,
        dest_id_1,
    ]

    # Stop using the default value for guild_id_3
    await ServerStatistics.update_population(guild_id_3, high_pop)

    await ServerStatistics.update_population(guild_id_1, high_pop + 1)
    dests_in_order = await MirroredChannel.fetch_dests(src_id)
    assert dests_in_order == [
        dest_id_1,
        dest_id_3,
        dest_id_2,
    ]

    await ServerStatistics.update_population_in_batch(
        [
            guild_id_3,
            guild_id_2,
            guild_id_1,
        ],
        [
            low_pop,
            medium_pop,
            high_pop,
        ],
    )
    dests_in_order = await MirroredChannel.fetch_dests(src_id)
    assert dests_in_order == [
        dest_id_1,
        dest_id_2,
        dest_id_3,
    ]


@pytest.mark.asyncio
async def test_add_and_fetch_mirror_srcs_cache(MirroredChannel: _MirroredChannel):
    src_id = 0
    src_id_2 = 4
    dest_id = 1
    dest_id_2 = 2
    guild_id = 3

    await assert_all_srcs_equals([], mirrored_channel=MirroredChannel)

    await MirroredChannel.add_mirror(src_id, dest_id, guild_id, legacy=True)
    await assert_all_srcs_equals([src_id], mirrored_channel=MirroredChannel)

    await MirroredChannel.add_mirror(src_id, dest_id_2, guild_id, legacy=True)
    await assert_all_srcs_equals([src_id], mirrored_channel=MirroredChannel)

    await MirroredChannel.add_mirror(src_id_2, dest_id_2, guild_id, legacy=False)
    # Added non legacy mirror, so should not be in cache
    await assert_all_srcs_equals([src_id], mirrored_channel=MirroredChannel)

    await MirroredChannel.add_mirror(src_id_2, dest_id, guild_id, legacy=True)
    # Added legacy mirror, so should be in cache
    await assert_all_srcs_equals([src_id, src_id_2], mirrored_channel=MirroredChannel)


@pytest.mark.asyncio
async def test_set_legacy_with_mirror_dests_cache(MirroredChannel: _MirroredChannel):
    src_id = 0
    dest_id = 1
    dest_id_2 = 5
    guild_id = 2

    await assert_all_srcs_equals([], mirrored_channel=MirroredChannel)

    await MirroredChannel.add_mirror(src_id, dest_id, guild_id, legacy=True)
    await assert_all_srcs_equals([src_id], mirrored_channel=MirroredChannel)

    await MirroredChannel.set_legacy(src_id, dest_id, True)
    await assert_all_srcs_equals([src_id], mirrored_channel=MirroredChannel)

    await MirroredChannel.add_mirror(src_id, dest_id_2, guild_id, legacy=True)
    await assert_all_srcs_equals([src_id], mirrored_channel=MirroredChannel)

    await MirroredChannel.set_legacy(src_id, dest_id, False)
    await assert_all_srcs_equals([src_id], mirrored_channel=MirroredChannel)

    await MirroredChannel.set_legacy(src_id, dest_id, True)
    await assert_all_srcs_equals([src_id], mirrored_channel=MirroredChannel)


@pytest.mark.asyncio
async def test_get_legacy_mirrors_disabled_for_failure(MirroredChannel):
    # Regression: this query filtered disabled mirrors with ``not cls.enabled``,
    # which Python evaluates to ``False`` at query-build time (the column object is
    # truthy), collapsing the WHERE to ``false`` so it always returned zero rows.
    # ``~cls.enabled`` emits the intended SQL ``NOT enabled``.
    disabled_src, disabled_dest = 10, 11
    enabled_src, enabled_dest = 20, 21
    guild_id = 99

    await MirroredChannel.add_mirror(disabled_src, disabled_dest, guild_id, legacy=True)
    await MirroredChannel.add_mirror(enabled_src, enabled_dest, guild_id, legacy=True)

    # Stamp the first mirror as disabled-for-failure in the past.
    stamp = dt.datetime(2024, 1, 1)
    async with schemas.db_session() as session, session.begin():
        await session.execute(
            update(MirroredChannel)
            .where(MirroredChannel.src_id == disabled_src)
            .values(enabled=False, legacy_disable_for_failure_on_date=stamp)
        )

    result = await MirroredChannel.get_legacy_mirrors_disabled_for_failure(
        dt.datetime(2023, 1, 1)
    )
    result_tuples = [tuple(row) for row in result]

    assert (disabled_src, disabled_dest) in result_tuples
    # The still-enabled mirror must be excluded.
    assert (enabled_src, enabled_dest) not in result_tuples


@pytest.mark.asyncio
async def test_undo_auto_disable_no_cartesian_overmatch(MirroredChannel):
    # Regression: undo_auto_disable_for_failure rebuilt its UPDATE WHERE as
    # ``src_id IN (...) AND dest_id IN (...)`` from the disabled pairs, which matches a
    # Cartesian product — so it also re-enabled off-diagonal rows that were
    # disabled for some *other* reason but shared a src or dest. It must re-enable only
    # rows actually disabled for failure since ``since``.
    s1, s2 = 100, 101
    d_a, d_b = 200, 201
    guild_id = 1
    for src, dest in ((s1, d_a), (s2, d_b), (s1, d_b), (s2, d_a)):
        await MirroredChannel.add_mirror(src, dest, guild_id, legacy=True)

    fail_stamp = dt.datetime(2024, 1, 1)
    async with schemas.db_session() as session, session.begin():
        # Diagonal: genuinely disabled *for failure*.
        await session.execute(
            update(MirroredChannel)
            .where(
                tuple_(MirroredChannel.src_id, MirroredChannel.dest_id).in_(
                    [(s1, d_a), (s2, d_b)]
                )
            )
            .values(enabled=False, legacy_disable_for_failure_on_date=fail_stamp)
        )
        # Off-diagonal: disabled, but NOT for failure (no stamp) — e.g. manually.
        await session.execute(
            update(MirroredChannel)
            .where(
                tuple_(MirroredChannel.src_id, MirroredChannel.dest_id).in_(
                    [(s1, d_b), (s2, d_a)]
                )
            )
            .values(enabled=False, legacy_disable_for_failure_on_date=None)
        )

    reenabled = await MirroredChannel.undo_auto_disable_for_failure(
        since=dt.datetime(2023, 1, 1)
    )
    assert {tuple(row) for row in reenabled} == {(s1, d_a), (s2, d_b)}

    async with schemas.db_session() as session, session.begin():
        rows = await session.execute(
            select(MirroredChannel.src_id, MirroredChannel.dest_id).where(
                MirroredChannel.enabled
            )
        )
    # Only the disabled-for-failure diagonal comes back; off-diagonal stays disabled.
    assert {tuple(row) for row in rows.fetchall()} == {(s1, d_a), (s2, d_b)}
