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

# DB-backed: RotationData JSON store + the lost_sector load_rotation resolution order
# (DB-first, gspread fallback, last-known-good cache). Uses the SQLite test DB from the
# package conftest.

import datetime as dt
import typing as t

import pytest

from dd.common import (
    lost_sector as ls_mod,
    schemas,
)
from dd.sector_accounting import sector_accounting

pytestmark = pytest.mark.asyncio

ZONES = [
    "Cosmodrome",
    "Dreaming City",
    "EDZ",
    "Europa",
    "Moon",
    "Neomuna",
    "Nessus",
    "Pale Heart",
    "Throne World",
]
DAY0 = dt.datetime(2023, 7, 20, 18, tzinfo=dt.UTC)


def _doc(first_sector: str = "Alpha") -> dict[str, t.Any]:
    return {
        "version": 1,
        "reference_date": "2023-07-20",
        "schedule": {z: [first_sector] for z in ZONES},
        "surge_cycle": [["Solar"]],
        "sectors": [
            {
                "name": first_sector,
                "shortlink_gfx": "https://x/a",
                "expert": {"champions": ["Barrier"], "shields": ["Arc"]},
                "master": {"champions": [], "shields": []},
            }
        ],
    }


async def test_rotation_data_get_set_roundtrip():
    assert await schemas.RotationData.get_data("missing_type") is None
    await schemas.RotationData.set_data("lost_sector", _doc("RoundTrip"))
    got = await schemas.RotationData.get_data("lost_sector")
    assert got is not None
    assert got["sectors"][0]["name"] == "RoundTrip"
    # Upsert overwrites in place.
    await schemas.RotationData.set_data("lost_sector", _doc("Replaced"))
    got2 = await schemas.RotationData.get_data("lost_sector")
    assert got2["sectors"][0]["name"] == "Replaced"


async def test_load_rotation_prefers_db_and_skips_gspread(
    monkeypatch: pytest.MonkeyPatch,
):
    ls_mod._rotation_cache.clear()
    await schemas.RotationData.set_data("lost_sector", _doc("FromDB"))

    def _no_gspread(*_args: t.Any, **_kwargs: t.Any) -> t.NoReturn:
        raise AssertionError("gspread fallback must not run when the DB has data")

    monkeypatch.setattr(sector_accounting.Rotation, "from_gspread_url", _no_gspread)

    rotation = await ls_mod.load_rotation()
    assert rotation(DAY0)[0].name == "FromDB"


async def test_load_rotation_serves_last_known_good_on_total_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    ls_mod._rotation_cache.clear()
    # Prime the cache via a successful DB-backed load.
    await schemas.RotationData.set_data("lost_sector", _doc("Cached"))
    await ls_mod.load_rotation()

    # Now the DB yields nothing and the gspread fallback fails: expect the cache.
    async def _no_row(_name: str) -> None:
        return None

    def _boom(*_args: t.Any, **_kwargs: t.Any) -> t.NoReturn:
        raise RuntimeError("sheet unreachable")

    monkeypatch.setattr(schemas.RotationData, "get_data", _no_row)
    monkeypatch.setattr(sector_accounting.Rotation, "from_gspread_url", _boom)

    rotation = await ls_mod.load_rotation()
    assert rotation(DAY0)[0].name == "Cached"
