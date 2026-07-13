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

# Schema registration + validation for the legacy world-activity rotations. Pure.

import typing as t

import fastjsonschema
import pytest

from dd.common import rotation_schema as rs


def _neomuna_doc() -> dict[str, t.Any]:
    return {
        "version": 1,
        "reference_date": "2026-04-21",
        "activities": [
            {
                "key": "vex_incursion",
                "title": "Vex Incursion Zone",
                "cadence": "weekly",
                "elements": [{"name": "zone", "values": ["Ahimsa Park"]}],
            },
            {
                "key": "story_mission",
                "title": "Story Mission",
                "cadence": "weekly",
                "elements": [{"name": "mission", "values": ["Desperate Measures"]}],
            },
            {
                "key": "partition",
                "title": "Partition",
                "cadence": "weekly",
                "elements": [{"name": "variant", "values": ["Ordnance"]}],
            },
            {
                "key": "terminal_overload",
                "title": "Terminal Overload",
                "cadence": "daily",
                "elements": [
                    {"name": "weapon", "values": ["Synchronic Roulette", "Circular"]},
                    {"name": "location", "values": ["Liming"]},
                ],
            },
        ],
    }


def test_every_destination_is_registered():
    for key in rs.LEGACY_DESTINATIONS:
        assert f"legacy_{key}" in rs.ROTATION_SCHEMAS


def test_valid_document_validates():
    rs.validate("legacy_neomuna", _neomuna_doc())


def test_independent_element_lengths_allowed():
    # weapon has 2 values, location has 1 — different lengths in one activity is fine.
    doc = _neomuna_doc()
    rs.validate("legacy_neomuna", doc)


def test_default_doc_matches_spec_structure():
    doc = rs.legacy_default_doc("legacy_neomuna")
    assert [a["key"] for a in doc["activities"]] == [
        "vex_incursion",
        "story_mission",
        "partition",
        "terminal_overload",
    ]
    assert doc["activities"][-1]["elements"] == [
        {"name": "weapon", "values": []},
        {"name": "location", "values": []},
    ]


def test_missing_activity_fails():
    doc = _neomuna_doc()
    doc["activities"].pop()
    with pytest.raises(fastjsonschema.JsonSchemaException):
        rs.validate("legacy_neomuna", doc)


def test_missing_element_fails():
    doc = _neomuna_doc()
    doc["activities"][-1]["elements"].pop()  # drop terminal_overload location
    with pytest.raises(fastjsonschema.JsonSchemaException):
        rs.validate("legacy_neomuna", doc)


def test_non_string_value_fails():
    doc = _neomuna_doc()
    doc["activities"][0]["elements"][0]["values"] = [123]
    with pytest.raises(fastjsonschema.JsonSchemaException):
        rs.validate("legacy_neomuna", doc)


def test_bad_reference_date_fails():
    doc = _neomuna_doc()
    doc["reference_date"] = "not-a-date"
    with pytest.raises(fastjsonschema.JsonSchemaException):
        rs.validate("legacy_neomuna", doc)


def test_tampered_const_element_name_fails():
    doc = _neomuna_doc()
    doc["activities"][0]["elements"][0]["name"] = "renamed"
    with pytest.raises(fastjsonschema.JsonSchemaException):
        rs.validate("legacy_neomuna", doc)


def _dares_doc() -> dict[str, t.Any]:
    return {
        "version": 1,
        "reference_date": "2026-04-21",
        "activities": [
            {
                "key": "rounds",
                "title": "Encounter Rounds",
                "cadence": "weekly",
                "elements": [
                    {"name": "first", "values": ["Fallen"]},
                    {"name": "second", "values": ["Hive"]},
                    {"name": "final", "values": ["Zydron"]},
                ],
            },
            {
                "key": "loot_table",
                "title": "Legendary Loot",
                "cadence": "weekly",
                "kind": "sets",
                "schedule": ["Set 1", "Set 2"],
                "sets": [
                    {
                        "name": "Set 1",
                        "weapons": ["Enigmas's Draw (Sidearm)"],
                        "armor": ["Wild Hunt", "Scatterhorn"],
                    },
                    {
                        "name": "Set 2",
                        "weapons": ["Far Future (Sniper Rifle)"],
                        "armor": ["Praefectus", "Scatterhorn"],
                    },
                ],
            },
        ],
    }


def test_dares_set_based_loot_validates():
    rs.validate("legacy_dares", _dares_doc())


def test_dares_loot_as_elements_is_rejected():
    # The set-based loot activity must not be an element-based one.
    doc = _dares_doc()
    doc["activities"][1] = {
        "key": "loot_table",
        "title": "Legendary Loot",
        "cadence": "weekly",
        "elements": [{"name": "weapon_1", "values": ["x"]}],
    }
    with pytest.raises(fastjsonschema.JsonSchemaException):
        rs.validate("legacy_dares", doc)


def test_dares_set_missing_armor_field_fails():
    doc = _dares_doc()
    del doc["activities"][1]["sets"][0]["armor"]
    with pytest.raises(fastjsonschema.JsonSchemaException):
        rs.validate("legacy_dares", doc)
