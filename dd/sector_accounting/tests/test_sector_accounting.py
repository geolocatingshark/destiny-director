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

# Pure parsing/rotation logic — driven from fixture data, no gspread/network.

import datetime as dt

import pytest

from dd.sector_accounting.sector_accounting import (
    DifficultySpecificSectorData,
    Rotation,
    Sector,
    SectorData,
)
from dd.sector_accounting.utils import EntityRotation, _parse_counts

# --- _parse_counts -------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", 0), ("?", -1), ("3", 3)],
)
def test_parse_counts(raw: str, expected: int):
    assert _parse_counts(raw) == expected


def test_parse_counts_rejects_garbage():
    with pytest.raises(ValueError):
        _parse_counts("nope")


# --- EntityRotation ------------------------------------------------------------


def test_entity_rotation_cycles_modulo_length():
    rot = EntityRotation(["a", "b", "c"])
    assert rot[0] == "a"
    assert rot[3] == "a"
    assert rot[4] == "b"
    assert rot[-1] == "c"


def test_entity_rotation_from_gspread_strips_header_and_trailing_blanks():
    # Column-major values; column 1, row 0 is the header, trailing "" dropped.
    values = [["h0", "x"], ["h1", "a", "b", ""]]
    rot = EntityRotation.from_gspread(values, 1)
    assert list(rot) == ["a", "b"]


# --- DifficultySpecificSectorData ----------------------------------------------


def test_difficulty_data_lists_present_champions_and_shields():
    data = DifficultySpecificSectorData(barrier_champions="1", arc_shields="1")
    assert data.champions_list == ["Barrier"]
    assert data.champions == "Barrier"
    assert data.shields_list == ["Arc"]
    assert data.shields == "Arc"
    assert bool(data) is True


def test_difficulty_data_empty_is_falsey_and_reads_none():
    data = DifficultySpecificSectorData()
    assert data.champions == "None"
    assert data.shields == "None"
    assert bool(data) is False


def test_difficulty_data_question_mark_counts_as_present():
    data = DifficultySpecificSectorData(unstoppable_champions="?")
    assert data.champions_list == ["Unstoppable"]
    assert bool(data) is True


# --- Sector --------------------------------------------------------------------


def test_sector_surges_split_on_commas_and_ampersands():
    sector = Sector(name="X", surge="Arc, Void & Solar")
    assert sector.surges == ["Arc", "Void", "Solar"]


def test_sector_add_fills_blanks_from_other():
    merged = Sector(name="X", reward="R") + Sector(name="X", surge="Arc")
    assert merged.reward == "R"
    assert merged.surge == "Arc"


def test_sector_add_requires_matching_name():
    with pytest.raises(ValueError):
        _ = Sector(name="A") + Sector(name="B")


# --- SectorData.gspread_data_row_to_sector -------------------------------------


def test_gspread_data_row_to_sector_maps_columns():
    general_row = ["Name", "Threat", "Weapon", "gfx"]
    #              [_,    void, solar, arc, stasis, strand, barrier, over, unstop, mods]
    legend_row = ["x", "0", "0", "1", "0", "0", "1", "0", "0", "Mods"]
    master_row = ["x", "0", "0", "0", "0", "0", "0", "0", "0", ""]

    sector = SectorData.gspread_data_row_to_sector(general_row, legend_row, master_row)

    assert sector.name == "Name"
    assert sector.threat == "Threat"
    assert sector.overcharged_weapon == "Weapon"
    assert sector.shortlink_gfx == "gfx"
    assert sector.expert_data.shields == "Arc"
    assert sector.expert_data.champions == "Barrier"
    assert bool(sector.master_data) is False


# --- Rotation.__call__ ---------------------------------------------------------


def _rotation() -> Rotation:
    start = dt.datetime(2024, 1, 1, tzinfo=dt.UTC)
    return Rotation(
        start_date=start,
        sector_rot={"Cosmodrome": EntityRotation(["A", "B"])},
        surge_rot=EntityRotation(["Arc", "Void"]),
        sector_data={"A": Sector(name="A", reward="rA"), "B": Sector(name="B")},
    )


def test_rotation_returns_day_zero_sector():
    rot = _rotation()
    [sector] = rot(dt.datetime(2024, 1, 1, tzinfo=dt.UTC))
    assert sector.name == "A"
    assert sector.surge == "Arc"
    assert sector.reward == "rA"


def test_rotation_advances_with_date():
    rot = _rotation()
    [sector] = rot(dt.datetime(2024, 1, 2, tzinfo=dt.UTC))
    assert sector.name == "B"
    assert sector.surge == "Void"
