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

"""Pure parsing tests for the Destiny models: hand-crafted manifest fixtures, no I/O."""

from dd.anchor.extensions.bungie_api.models import (
    DestinyArmor,
    DestinyItem,
    DestinyWeapon,
)

# Minimal manifest covering only the tables/hashes the weapon sale item touches.
_WEAPON_MANIFEST = {
    "DestinyInventoryItemDefinition": {
        1001: {
            "displayProperties": {"name": "Test Hand Cannon"},
            "inventory": {"tierTypeName": "Legendary", "bucketTypeHash": 9001},
            "classType": 1,  # Hunter
            "itemType": 3,  # weapon
            "itemTypeDisplayName": "Hand Cannon",
        },
        5001: {"displayProperties": {"name": "Glimmer"}},
    },
    "DestinyEquipmentSlotDefinition": {
        9001: {"displayProperties": {"name": "Kinetic Weapons"}},
    },
}


def test_from_sale_item_builds_weapon():
    sale_item = {
        "itemHash": 1001,
        "costs": [{"itemHash": 5001, "quantity": 1000}],
    }

    item = DestinyItem.from_sale_item(
        sale_item=sale_item,
        stats={},
        perks={},
        manifest_table=_WEAPON_MANIFEST,
    )

    assert isinstance(item, DestinyWeapon)
    assert item.is_weapon and not item.is_armor
    assert item.name == "Test Hand Cannon"
    assert item.rarity == "Legendary"
    assert item.is_legendary
    assert item.class_ == "Hunter"
    assert item.bucket == "Kinetic Weapons"
    assert item.costs == {"Glimmer": 1000}
    # No collectibleHash in the manifest entry → no collection set.
    assert item.collectible_set_name is None


def test_destiny_armor_maps_v2_stat_names_to_v3():
    # DestinyArmor accepts legacy (v2) stat names and exposes the v3 stat set,
    # summing any matching v2/v3 inputs per slot.
    armor = DestinyArmor(
        name="Test Helm",
        hash_=2002,
        rarity="Legendary",
        class_="Hunter",
        bucket="Helmet",
        item_type=2,  # armor
        item_type_friendly_name="Helmet",
        stats={"Mobility": 10, "Resilience": 20, "Recovery": 30},
    )

    assert armor.is_armor
    assert armor.stats == {
        "Weapons": 10,  # <- Mobility
        "Health": 20,  # <- Resilience
        "Class": 30,  # <- Recovery
        "Grenade": 0,
        "Super": 0,
        "Melee": 0,
    }
    assert armor.stat_total == 60
