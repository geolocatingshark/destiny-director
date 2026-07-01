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

# Pure mapping logic for the DB-JSON rotation store — Rotation.from_json / to_json.
# No DB or network.

import datetime as dt
import typing as t

import pytest

from dd.sector_accounting.sector_accounting import Rotation

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

# Comfortably after any reset offset on the reference day, so day index is 0.
DAY0 = dt.datetime(2023, 7, 20, 18, tzinfo=dt.UTC)


def _doc(**overrides: t.Any) -> dict[str, t.Any]:
    doc: dict[str, t.Any] = {
        "version": 1,
        "reference_date": "2023-07-20",
        "schedule": {z: ["Alpha", "Bravo"] for z in ZONES},
        "sectors": [
            {
                "name": "Alpha",
                "shortlink_gfx": "https://x/a",
                "expert": {"champions": ["Barrier"], "shields": ["Arc"]},
                "master": {
                    "champions": ["Barrier", "Overload"],
                    "shields": ["Arc", "Void"],
                },
            },
            {
                "name": "Bravo",
                "shortlink_gfx": "https://x/b",
                "expert": {"champions": [], "shields": []},
                "master": {"champions": ["Unstoppable"], "shields": ["Strand"]},
            },
        ],
    }
    doc.update(overrides)
    return doc


def test_from_json_start_date_applies_reset_offset():
    # Reference day at 16h + (60 - buffer)min == 16:55 UTC for buffer=5.
    rot = Rotation.from_json(_doc(), buffer=5)
    assert rot.start_date == dt.datetime(2023, 7, 20, 16, 55, tzinfo=dt.UTC)


def test_from_json_day_zero_sectors():
    rot = Rotation.from_json(_doc())
    sectors = rot(DAY0)
    assert len(sectors) == len(ZONES)
    first = sectors[0]
    assert first.name == "Alpha"
    assert first.shortlink_gfx == "https://x/a"
    assert first.expert_data.champions_list == ["Barrier"]
    assert first.master_data.shields_list == ["Arc", "Void"]


def test_from_json_cycles_to_next_day():
    rot = Rotation.from_json(_doc())
    bravo = rot(DAY0 + dt.timedelta(days=1))[0]
    assert bravo.name == "Bravo"
    # Absent presence -> empty; present -> listed.
    assert bravo.expert_data.champions_list == []
    assert bravo.master_data.champions_list == ["Unstoppable"]
    assert bravo.master_data.shields_list == ["Strand"]


def test_scheduled_name_absent_from_sectors_raises_keyerror():
    # Mirrors from_gspread's "TBC" path: __call__ raises KeyError, handled upstream.
    rot = Rotation.from_json(_doc(schedule={z: ["Ghost"] for z in ZONES}))
    with pytest.raises(KeyError):
        rot(DAY0)


def _render(rot: Rotation, date: dt.datetime) -> list[t.Any]:
    return [
        (
            s.name,
            s.shortlink_gfx,
            s.expert_data.champions_list,
            s.expert_data.shields_list,
            s.master_data.champions_list,
            s.master_data.shields_list,
        )
        for s in rot(date)
    ]


def test_to_json_from_json_rendered_parity_across_cycle():
    rot = Rotation.from_json(_doc())
    back = Rotation.from_json(rot.to_json())
    for k in range(8):
        date = DAY0 + dt.timedelta(days=k)
        assert _render(rot, date) == _render(back, date)


def test_to_json_shape():
    out = Rotation.from_json(_doc()).to_json()
    assert out["version"] == 1
    assert out["reference_date"] == "2023-07-20"
    assert set(out["schedule"]) == set(ZONES)
    assert "surge_cycle" not in out
    assert out["sectors"][0]["expert"] == {"champions": ["Barrier"], "shields": ["Arc"]}
    assert "threat" not in out["sectors"][0]
    assert out["sectors"][1]["master"] == {
        "champions": ["Unstoppable"],
        "shields": ["Strand"],
    }
