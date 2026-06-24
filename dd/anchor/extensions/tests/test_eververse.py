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
    _eververse_type_group,
    _exotic_ornament_target_name,
    _group_eververse_offerings,
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


def _item_of(
    class_: str, type_name: str, name: str = "X", hash_: int = 1
) -> DestinyItem:
    return DestinyItem(
        name=name,
        hash_=hash_,
        rarity="Legendary",
        class_=class_,
        bucket="",
        item_type=2,
        item_type_friendly_name=type_name,
    )


def test_eververse_type_group_buckets_class_specific_and_types():
    # Class-specific armor ornaments → one Armor Ornaments group regardless of type.
    assert _eververse_type_group(_item_of("Titan", "Titan Ornament"))[1:] == (
        "armor",
        "Armor Ornaments",
    )
    # Class-agnostic items → curated type group, or pluralised type name as fallback.
    assert _eververse_type_group(_item_of("Unknown", "Weapon Ornament"))[1:] == (
        "weapon",
        "Weapon Ornaments",
    )
    assert _eververse_type_group(_item_of("Unknown", "Ghost Shell"))[1:] == (
        "ghost",
        "Ghosts",
    )
    assert _eververse_type_group(_item_of("Unknown", "Ship"))[1:] == (
        "sparrow",
        "Vehicles & Sparrows",
    )
    assert _eververse_type_group(_item_of("Unknown", "Shader")) == (4, "", "Shaders")


def test_group_eververse_offerings_orders_groups_and_sorts_items():
    items = [
        _item_of("Unknown", "Shader", name="Zeta Shader", hash_=10),
        _item_of("Hunter", "Hunter Ornament", name="Hrafnagud", hash_=11),
        _item_of("Titan", "Titan Ornament", name="Arcturus Engine", hash_=12),
        _item_of("Unknown", "Ghost Shell", name="Drilldown Shell", hash_=13),
    ]
    groups = _group_eververse_offerings(items)
    headers = [header for _emoji, header, _items in groups]
    # Curated rank: Armor Ornaments, then Ghosts, then the fallback Shaders group.
    assert headers == ["Armor Ornaments", "Ghosts", "Shaders"]
    armor = next(items for _e, h, items in groups if h == "Armor Ornaments")
    # Items within a group are sorted by name.
    assert [i.name for i in armor] == ["Arcturus Engine", "Hrafnagud"]
