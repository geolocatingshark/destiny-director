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

# Pure tests for the Xur location/armour data structures — no gspread/network.

from dd.sector_accounting.xur import (
    XurArmorSet,
    XurArmorSets,
    XurLocation,
    XurLocations,
)

# --- XurLocation.__str__ -------------------------------------------------------


def test_location_str_uses_api_name_by_default():
    assert str(XurLocation(api_location_name="edz")) == "edz"


def test_location_str_prefers_friendly_name():
    assert str(XurLocation("edz", "The EDZ")) == "The EDZ"


def test_location_str_wraps_in_markdown_link():
    loc = XurLocation("edz", "The EDZ", "https://example.com")
    assert str(loc) == "[The EDZ](https://example.com)"


# --- XurLocations.__getitem__ --------------------------------------------------


def test_locations_returns_stored_entry():
    locs = XurLocations()
    locs["edz"] = XurLocation("edz", "The EDZ")
    assert locs["edz"].friendly_location_name == "The EDZ"


def test_locations_unknown_key_returns_default():
    locs = XurLocations()
    default = locs["nowhere"]
    assert default.api_location_name == "nowhere"
    assert default.friendly_location_name is None


# --- XurLocations.from_json / to_json ------------------------------------------


def _doc(**overrides):
    doc = {
        "version": 1,
        "locations": [
            {
                "api_location_name": "Nessus, Watcher's Grave",
                "friendly_location_name": "Watcher's Grave, Nessus",
                "link": "https://kyber3000.com/x",
            }
        ],
    }
    doc.update(overrides)
    return doc


def test_from_json_builds_mapping():
    locs = XurLocations.from_json(_doc())
    loc = locs["Nessus, Watcher's Grave"]
    assert loc.friendly_location_name == "Watcher's Grave, Nessus"
    assert loc.link == "https://kyber3000.com/x"


def test_from_json_missing_friendly_and_link_falls_back_to_api_name():
    locs = XurLocations.from_json(
        {"version": 1, "locations": [{"api_location_name": "edz"}]}
    )
    assert str(locs["edz"]) == "edz"


def test_from_json_blank_strings_normalised_to_none():
    locs = XurLocations.from_json(
        {
            "version": 1,
            "locations": [
                {"api_location_name": "edz", "friendly_location_name": "", "link": ""}
            ],
        }
    )
    loc = locs["edz"]
    assert loc.friendly_location_name is None
    assert loc.link is None


def test_from_json_tolerates_absent_locations_key():
    assert XurLocations.from_json({"version": 1}) == {}


def test_to_json_round_trips_via_from_json():
    original = XurLocations.from_json(_doc())
    doc = original.to_json()
    assert doc["version"] == 1
    rebuilt = XurLocations.from_json(doc)
    assert str(rebuilt["Nessus, Watcher's Grave"]) == str(
        original["Nessus, Watcher's Grave"]
    )


def test_to_json_omits_blank_friendly_and_link():
    locs = XurLocations()
    locs["edz"] = XurLocation("edz")
    assert locs.to_json()["locations"] == [{"api_location_name": "edz"}]


# --- XurArmorSet / XurArmorSets ------------------------------------------------


def test_armor_set_str_wraps_in_markdown_link():
    armor = XurArmorSet(friendly_name="Set", link="https://example.com")
    assert str(armor) == "[Set](https://example.com)"


def test_armor_set_str_without_link():
    assert str(XurArmorSet(friendly_name="Set")) == "Set"


def test_armor_sets_unknown_key_returns_default():
    sets = XurArmorSets()
    default = sets["unknown"]
    assert default.friendly_name == "unknown"
