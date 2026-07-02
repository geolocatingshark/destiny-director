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

# DB-backed: the xur_location load resolution order (DB-first, gspread fallback,
# last-known-good cache), mirroring dd/beacon/tests/test_rotation_data_and_load.py.
# Uses the SQLite test DB from the package conftest.

import typing as t

import pytest

from dd.anchor.extensions import xur as xur_ext
from dd.common import schemas
from dd.sector_accounting import xur as xur_data

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


async def test_load_prefers_db_and_skips_gspread(monkeypatch: pytest.MonkeyPatch):
    xur_ext._xur_locations_cache.clear()
    await schemas.RotationData.set_data("xur_location", _doc("FromDB"))

    def _no_gspread(*_args: t.Any, **_kwargs: t.Any) -> t.NoReturn:
        raise AssertionError("gspread fallback must not run when the DB has data")

    monkeypatch.setattr(xur_data.XurLocations, "from_gspread_url", _no_gspread)

    locations = await xur_ext.load_xur_locations()
    assert locations["FromDB"].friendly_location_name == "Watcher's Grave, Nessus"


async def test_load_serves_last_known_good_on_total_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    xur_ext._xur_locations_cache.clear()
    # Prime the cache via a successful DB-backed load.
    await schemas.RotationData.set_data("xur_location", _doc("Cached"))
    await xur_ext.load_xur_locations()

    # Now the DB yields nothing and the gspread fallback fails: expect the cache.
    async def _no_row(_name: str) -> None:
        return None

    def _boom(*_args: t.Any, **_kwargs: t.Any) -> t.NoReturn:
        raise RuntimeError("sheet unreachable")

    monkeypatch.setattr(schemas.RotationData, "get_data", _no_row)
    monkeypatch.setattr(xur_data.XurLocations, "from_gspread_url", _boom)

    locations = await xur_ext.load_xur_locations()
    assert "Cached" in locations
