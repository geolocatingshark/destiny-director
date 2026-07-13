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

# DB-backed load_rotation resolution + the pure page-window builder for /legacy. Uses
# the SQLite test DB from the beacon package conftest.

import datetime as dt
import typing as t

import hikari as h
import pytest

from dd.beacon.extensions import legacy_activities as cmd
from dd.common import (
    components,
    legacy_activities as loader_mod,
    schemas,
)

pytestmark = pytest.mark.asyncio

NOW = dt.datetime(2026, 4, 21, 18, tzinfo=dt.UTC)


def _doc() -> dict[str, t.Any]:
    return {
        "version": 1,
        "reference_date": "2026-04-21",
        "activities": [
            {
                "key": "terminal_overload",
                "title": "Terminal Overload",
                "cadence": "daily",
                "elements": [
                    {"name": "weapon", "values": ["Circular Logic"]},
                    {"name": "location", "values": ["Zephyr"]},
                ],
            }
        ],
    }


async def test_load_rotation_reads_from_db():
    await schemas.RotationData.set_data("legacy_neomuna", _doc())
    rotation = await loader_mod.load_rotation("neomuna")
    resolved = {r.key: r.values for r in rotation(NOW)}
    assert resolved["terminal_overload"] == {
        "weapon": "Circular Logic",
        "location": "Zephyr",
    }


async def test_load_rotation_serves_cache_when_db_blank():
    # Prime the cache with a good load, then a slug whose row is absent still serves it.
    await schemas.RotationData.set_data("legacy_moon", _doc())
    first = await loader_mod.load_rotation("moon")
    # Wipe the row; the loader must fall back to the last-known-good cache.
    await schemas.RotationData.set_data("legacy_moon", {"invalid": True})
    served = await loader_mod.load_rotation("moon")
    assert served.to_json() == first.to_json()


async def test_load_rotation_missing_raises():
    with pytest.raises(RuntimeError):
        await loader_mod.load_rotation("europa")


async def test_build_pages_daily_window():
    rotation = loader_mod.LegacyRotation.from_json(_doc())
    pages = await cmd.build_pages("neomuna", rotation, {}, now=NOW)
    assert len(pages) == cmd._DAILY_PAGE_COUNT
    # Each factory is sync and yields a non-empty component list.
    factory = t.cast(components.Cv2PageFactory, pages[0])
    first = factory()
    assert first and hasattr(first[0], "components")


async def test_build_pages_weekly_window():
    doc = _doc()
    doc["activities"][0]["cadence"] = "weekly"
    rotation = loader_mod.LegacyRotation.from_json(doc)
    pages = await cmd.build_pages("neomuna", rotation, {}, now=NOW)
    assert len(pages) == cmd._WEEKLY_PAGE_COUNT


async def test_build_pages_factories_are_fresh_per_call():
    # The Paginator injects its nav row into the returned container on every render, so
    # a revisited page (paging backwards) is re-rendered. A cached builder would pile up
    # nav rows and Discord would reject the edit ("interaction failed"); each call must
    # therefore build a fresh container.
    rotation = loader_mod.LegacyRotation.from_json(_doc())
    pages = await cmd.build_pages("neomuna", rotation, {}, now=NOW)
    factory = t.cast(components.Cv2PageFactory, pages[0])

    first = factory()
    container = t.cast(h.impl.ContainerComponentBuilder, first[0])
    container.add_action_row(components.nav_buttons_row(page_index=0, page_count=2))
    second = factory()

    assert second[0] is not first[0]  # a new builder each call
    fresh = t.cast(h.impl.ContainerComponentBuilder, second[0])
    assert len(fresh.components) == 1  # only the text display, no leaked nav row


async def test_build_week_pages_count_and_fresh():
    rotation = loader_mod.LegacyRotation.from_json(_doc())
    pages = await cmd.build_week_pages("neomuna", rotation, {}, now=NOW)
    assert len(pages) == cmd._WEEK_DAILY_PAGE_COUNT
    factory = t.cast(components.Cv2PageFactory, pages[0])
    assert factory()[0] is not factory()[0]  # a fresh container each call
