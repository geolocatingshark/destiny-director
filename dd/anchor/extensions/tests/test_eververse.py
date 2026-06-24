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

"""Pure-helper tests for the eververse daily bright-dust offerings.

No DB / network / Discord — only the manifest-driven pure functions, exercised with
hand-built fixtures that mirror the live manifest shapes (verified against dev data).
"""

from dd.anchor.extensions.bungie_api.models import DestinyItem
from dd.anchor.extensions.eververse import (
    _bright_dust_rotator_hashes,
    _exotic_ornament_target_name,
)


def _vendor_manifest() -> dict:
    return {
        "DestinyVendorDefinition": {
            10: {
                "hash": 10,
                "vendorIdentifier": "EVERVERSE_BRIGHT_DUST_ROTATOR_EXOTIC_GHOSTS",
            },
            20: {
                "hash": 20,
                "vendorIdentifier": "EVERVERSE_BRIGHT_DUST_ROTATOR_LEGENDARY_SHADERS",
            },
            30: {"hash": 30, "vendorIdentifier": "EVERVERSE_WEEKLY_OFFERINGS"},
            40: {"hash": 40, "vendorIdentifier": ""},
            50: {"hash": 50},  # missing vendorIdentifier entirely
        }
    }


def test_bright_dust_rotator_hashes_filters_by_prefix():
    hashes = _bright_dust_rotator_hashes(_vendor_manifest())
    assert sorted(hashes) == [10, 20]


def test_bright_dust_rotator_hashes_empty_when_none_match():
    manifest = {
        "DestinyVendorDefinition": {
            1: {"hash": 1, "vendorIdentifier": "SOMETHING_ELSE"},
        }
    }
    assert _bright_dust_rotator_hashes(manifest) == []


def _item_manifest_with(entry: dict) -> dict:
    return {"DestinyInventoryItemDefinition": {999: entry}}


def _item() -> DestinyItem:
    return DestinyItem(
        name="Test Ornament",
        hash_=999,
        rarity="Exotic",
        class_="Hunter",
        bucket="",
        item_type=19,
        item_type_friendly_name="Hunter Ornament",
    )


def test_exotic_armor_ornament_target_resolved():
    manifest = _item_manifest_with(
        {
            "traitIds": ["item.ornament.armor"],
            "displayProperties": {
                "description": (
                    "Equip this ornament to change the appearance of "
                    "Celestial Nighthawk. Once you get an ornament, it's unlocked."
                )
            },
        }
    )
    assert _exotic_ornament_target_name(_item(), manifest) == "Celestial Nighthawk"


def test_exotic_weapon_ornament_target_resolved():
    manifest = _item_manifest_with(
        {
            "traitIds": ["item.ornament.weapon"],
            "displayProperties": {
                "description": (
                    "Equip this weapon ornament to change the appearance of "
                    "Heartshadow. Once you get an ornament, it's unlocked."
                )
            },
        }
    )
    assert _exotic_ornament_target_name(_item(), manifest) == "Heartshadow"


def test_non_ornament_exotic_returns_none():
    # A ghost shell / ship is exotic but not an ornament: no base item, no suffix.
    manifest = _item_manifest_with(
        {
            "traitIds": ["item.ghost"],
            "displayProperties": {
                "description": "For Ghosts who get to the heart of the matter."
            },
        }
    )
    assert _exotic_ornament_target_name(_item(), manifest) is None


def test_ornament_without_matching_description_returns_none():
    manifest = _item_manifest_with(
        {
            "traitIds": ["item.ornament.armor"],
            "displayProperties": {"description": "A mysterious ornament."},
        }
    )
    assert _exotic_ornament_target_name(_item(), manifest) is None


def test_missing_manifest_entry_returns_none():
    assert (
        _exotic_ornament_target_name(_item(), {"DestinyInventoryItemDefinition": {}})
        is None
    )
