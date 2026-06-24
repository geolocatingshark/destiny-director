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
