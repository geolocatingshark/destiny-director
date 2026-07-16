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

# DB-backed: the xur_location load resolution order (DB row → committed-seed auto-seed →
# last-known-good cache → empty-map degrade), mirroring
# dd/beacon/tests/test_rotation_data_and_load.py. Uses the SQLite test DB from the
# package conftest.

import typing as t

import pytest

from dd.anchor.extensions import xur as xur_ext
from dd.common import schemas

pytestmark = pytest.mark.asyncio


def _doc(name: str = "Nessus, Watcher's Grave") -> dict[str, t.Any]:
    return {
        "version": 1,
        "locations": [
            {
                "api_location_name": name,
                "friendly_location_name": "Watcher's Grave, Nessus",
                "link": "https://kyber3000.com/x",
            }
        ],
    }


async def test_load_reads_from_db():
    xur_ext._xur_locations_cache.clear()
    await schemas.RotationData.set_data("xur_location", _doc("FromDB"))

    locations = await xur_ext.load_xur_locations()
    assert locations["FromDB"].friendly_location_name == "Watcher's Grave, Nessus"


async def test_load_auto_seeds_absent_row(monkeypatch: pytest.MonkeyPatch):
    xur_ext._xur_locations_cache.clear()
    # No DB row; the committed seed doc supplies the mapping and is persisted.
    monkeypatch.setattr(xur_ext, "_load_seed_doc", lambda: _doc("Seeded"))

    async def _no_row(_name: str) -> None:
        return None

    monkeypatch.setattr(schemas.RotationData, "get_data", _no_row)

    locations = await xur_ext.load_xur_locations()
    assert locations["Seeded"].friendly_location_name == "Watcher's Grave, Nessus"


async def test_load_serves_last_known_good_on_db_error(
    monkeypatch: pytest.MonkeyPatch,
):
    xur_ext._xur_locations_cache.clear()
    # Prime the cache via a successful DB-backed load.
    await schemas.RotationData.set_data("xur_location", _doc("Cached"))
    await xur_ext.load_xur_locations()

    # A transient DB error (not a clean absent read) must serve the cache, not re-seed.
    async def _boom(_name: str) -> t.NoReturn:
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(schemas.RotationData, "get_data", _boom)

    locations = await xur_ext.load_xur_locations()
    assert "Cached" in locations


async def test_load_degrades_to_empty_map_without_row_or_seed(
    monkeypatch: pytest.MonkeyPatch,
):
    xur_ext._xur_locations_cache.clear()
    monkeypatch.setattr(xur_ext, "_load_seed_doc", lambda: None)

    async def _no_row(_name: str) -> None:
        return None

    monkeypatch.setattr(schemas.RotationData, "get_data", _no_row)

    locations = await xur_ext.load_xur_locations()
    # Empty map still renders — __getitem__ falls back to the raw API location name.
    assert str(locations["Tower, Hangar"]) == "Tower, Hangar"
