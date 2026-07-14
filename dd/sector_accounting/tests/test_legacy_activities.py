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

# Pure cyclic-indexing logic for the legacy-activity rotations. Each element rotates
# independently with its own length. No DB or network.

import datetime as dt
import typing as t

from dd.sector_accounting.legacy_activities import LegacyRotation
from dd.sector_accounting.utils import CyclicList

# 2025-01-07 is a Tuesday (weekly reset); the reset offset lands day 0 at 17:00 UTC.
REF = "2025-01-07"
DAY0 = dt.datetime(2025, 1, 7, 18, tzinfo=dt.UTC)


def _doc() -> dict[str, t.Any]:
    return {
        "version": 1,
        "reference_date": REF,
        "activities": [
            {
                "key": "mixed",
                "title": "Mixed",
                "cadence": "daily",
                # weapon: 2-cycle; location: 3-cycle — independent lengths.
                "elements": [
                    {"name": "weapon", "values": ["A", "B"]},
                    {"name": "location", "values": ["X", "Y", "Z"]},
                ],
            },
            {
                "key": "weekly_thing",
                "title": "Weekly Thing",
                "cadence": "weekly",
                "elements": [{"name": "zone", "values": ["P", "Q"]}],
            },
            {
                "key": "empty_thing",
                "title": "Empty Thing",
                "cadence": "daily",
                "elements": [{"name": "value", "values": []}],
            },
        ],
    }


def _values(rotation: LegacyRotation, date: dt.datetime) -> dict[str, t.Any]:
    return {r.key: r.values for r in rotation(date)}


def test_cyclic_list_wraps_forward_and_backward():
    cl = CyclicList(["a", "b", "c"])
    assert [cl[i] for i in range(6)] == ["a", "b", "c", "a", "b", "c"]
    # Negative indices wrap forward (Python % follows the divisor's sign).
    assert cl[-1] == "c"
    assert cl[-3] == "a"


def test_elements_rotate_independently_by_their_own_length():
    rot = LegacyRotation.from_json(_doc())
    weapon = [
        _values(rot, DAY0 + dt.timedelta(days=n))["mixed"]["weapon"] for n in range(4)
    ]
    location = [
        _values(rot, DAY0 + dt.timedelta(days=n))["mixed"]["location"] for n in range(4)
    ]
    assert weapon == ["A", "B", "A", "B"]  # 2-cycle
    assert location == ["X", "Y", "Z", "X"]  # 3-cycle, same activity


def test_weekly_stable_within_week_flips_on_boundary():
    rot = LegacyRotation.from_json(_doc())
    week0 = {
        _values(rot, DAY0 + dt.timedelta(days=n))["weekly_thing"]["zone"]
        for n in range(7)
    }
    assert week0 == {"P"}
    assert _values(rot, DAY0 + dt.timedelta(days=7))["weekly_thing"] == {"zone": "Q"}
    assert _values(rot, DAY0 + dt.timedelta(days=14))["weekly_thing"] == {"zone": "P"}


def test_empty_element_resolves_to_blank():
    rot = LegacyRotation.from_json(_doc())
    resolved = rot(DAY0)
    empty = next(r for r in resolved if r.key == "empty_thing")
    assert empty.values == {"value": ""}
    assert empty.is_empty


def test_step_is_daily_when_any_activity_is_daily():
    assert LegacyRotation.from_json(_doc()).step == dt.timedelta(days=1)


def test_step_is_weekly_when_all_weekly():
    doc = {
        "version": 1,
        "reference_date": REF,
        "activities": [
            {
                "key": "w",
                "title": "W",
                "cadence": "weekly",
                "elements": [{"name": "v", "values": ["1"]}],
            }
        ],
    }
    assert LegacyRotation.from_json(doc).step == dt.timedelta(days=7)


def test_from_json_to_json_round_trips():
    rot = LegacyRotation.from_json(_doc())
    assert LegacyRotation.from_json(rot.to_json()).to_json() == rot.to_json()


def test_to_json_preserves_reference_date():
    rot = LegacyRotation.from_json(_doc())
    assert rot.to_json()["reference_date"] == REF


def _sets_doc() -> dict[str, t.Any]:
    return {
        "version": 1,
        "reference_date": REF,
        "activities": [
            {
                "key": "loot",
                "title": "Loot",
                "cadence": "weekly",
                "kind": "sets",
                "schedule": ["Set A", "Set B"],
                "sets": [
                    {"name": "Set A", "weapons": ["W1", "W2"], "armor": ["A1"]},
                    {"name": "Set B", "weapons": ["W3"], "armor": ["A2", "A3"]},
                ],
            }
        ],
    }


def _live_set(rotation: LegacyRotation, date: dt.datetime):
    live = rotation(date)[0].set
    assert live is not None
    return live


def test_set_based_schedule_selects_the_live_set_by_week():
    rot = LegacyRotation.from_json(_sets_doc())
    week0 = _live_set(rot, DAY0)
    assert (week0.name, week0.weapons, week0.armor) == ("Set A", ["W1", "W2"], ["A1"])
    # Stable across the week, then flips to Set B on the next reset.
    assert _live_set(rot, DAY0 + dt.timedelta(days=6)).name == "Set A"
    assert _live_set(rot, DAY0 + dt.timedelta(days=7)).name == "Set B"
    # Schedule of length 2 wraps back to Set A two weeks on.
    assert _live_set(rot, DAY0 + dt.timedelta(days=14)).name == "Set A"


def test_set_based_round_trips():
    rot = LegacyRotation.from_json(_sets_doc())
    assert LegacyRotation.from_json(rot.to_json()).to_json() == rot.to_json()


def test_empty_set_schedule_resolves_to_tbc():
    doc = _sets_doc()
    doc["activities"][0]["schedule"] = []
    resolved = LegacyRotation.from_json(doc)(DAY0)[0]
    assert resolved.set is None
    assert resolved.is_empty


def test_missing_scheduled_set_is_tbc_not_error():
    doc = _sets_doc()
    doc["activities"][0]["schedule"] = ["ghost"]  # id not in the set pool
    resolved = LegacyRotation.from_json(doc)(DAY0)[0]
    assert resolved.set is None
    assert resolved.is_empty


def test_duplicate_set_names_are_rejected():
    # Sets are keyed by name, so two sets sharing a name would silently collapse (the
    # last wins) and the schedule would resolve to the wrong gear. from_json must reject
    # the document instead (review finding #6).
    import pytest

    doc = _sets_doc()
    doc["activities"][0]["sets"].append(
        {"name": "Set A", "weapons": ["W9"], "armor": ["A9"]}  # duplicate of Set A
    )
    with pytest.raises(ValueError, match="duplicate set names"):
        LegacyRotation.from_json(doc)


def test_item_links_round_trip():
    doc = _doc()
    doc["item_links"] = {"A (Auto Rifle)": "https://www.light.gg/db/items/1/"}
    rot = LegacyRotation.from_json(doc)
    assert rot.item_links == {"A (Auto Rifle)": "https://www.light.gg/db/items/1/"}
    assert rot.to_json()["item_links"] == rot.item_links


def test_item_links_absent_by_default():
    rot = LegacyRotation.from_json(_doc())
    assert rot.item_links == {}
    assert "item_links" not in rot.to_json()  # omitted when empty
