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

"""The projection read path, end to end: manifest JSON in, a parsed vendor out.

This is the join the port rests on. The definitions go in as Bungie ships them, through
the real ingest, out through :class:`ManifestLookup`'s hydration, and into the
unmodified :mod:`.models` parsers — so a field the projection drops, or hydrates under
the wrong key, shows up here as a wrong ``DestinyVendor`` rather than at 5pm Tuesday.
"""

from __future__ import annotations

import typing as t
from pathlib import Path

import pytest
import pytest_asyncio

from dd.anchor.extensions.bungie_api import manifest_db, models
from dd.anchor.tests.manifest_projection import clear_projection, load_projection

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

VENDOR_HASH = 2190858386
WEAPON_HASH = 1001
ARMOUR_HASH = 1002
GLIMMER_HASH = 5001

DEFINITIONS: dict[str, list[dict[str, t.Any]]] = {
    "DestinyVendorDefinition": [
        {
            "hash": VENDOR_HASH,
            "displayProperties": {"name": "Xûr"},
            "vendorIdentifier": "XUR",
            "locations": [{"destinationHash": 800}, {"destinationHash": 801}],
        }
    ],
    "DestinyDestinationDefinition": [
        {"hash": 800, "displayProperties": {"name": "The Tower"}},
        {"hash": 801, "displayProperties": {"name": "Nessus"}},
    ],
    "DestinyInventoryItemDefinition": [
        {
            "hash": WEAPON_HASH,
            "displayProperties": {"name": "Test Hand Cannon", "icon": "/hc.png"},
            "inventory": {"tierTypeName": "Legendary", "bucketTypeHash": 9001},
            "classType": 1,
            "itemType": 3,
            "itemTypeDisplayName": "Hand Cannon",
        },
        {
            "hash": ARMOUR_HASH,
            "displayProperties": {"name": "Test Helm"},
            "inventory": {"tierTypeName": "Legendary", "bucketTypeHash": 9002},
            "classType": 1,
            "itemType": 2,
            "itemTypeDisplayName": "Helmet",
            "collectibleHash": 500,
        },
        # A currency: not a weapon or armour, and the reason the projection covers
        # every item rather than just those two.
        {
            "hash": GLIMMER_HASH,
            "displayProperties": {"name": "Glimmer"},
            "itemType": 0,
            "inventory": {"tierTypeName": "Common"},
        },
    ],
    "DestinyEquipmentSlotDefinition": [
        {"hash": 9001, "displayProperties": {"name": "Kinetic Weapons"}},
        {"hash": 9002, "displayProperties": {"name": "Helmet Armor"}},
    ],
    "DestinyCollectibleDefinition": [
        {
            "hash": 500,
            "displayProperties": {"name": "Test Helm"},
            "itemHash": ARMOUR_HASH,
            "parentNodeHashes": [600],
        }
    ],
    "DestinyPresentationNodeDefinition": [
        {"hash": 600, "displayProperties": {"name": "Wild Hunt Suit"}}
    ],
    "DestinyStatDefinition": [{"hash": 20, "displayProperties": {"name": "Impact"}}],
    "DestinySandboxPerkDefinition": [
        {"hash": 30, "displayProperties": {"name": "Rampage"}}
    ],
}


def _vendor_response() -> dict[str, t.Any]:
    """A ``fetch_vendor`` response over the definitions above."""
    return {
        "vendor": {"data": {"vendorHash": VENDOR_HASH, "vendorLocationIndex": 1}},
        "sales": {
            "data": {
                "1": {
                    "itemHash": WEAPON_HASH,
                    "costs": [{"itemHash": GLIMMER_HASH, "quantity": 1000}],
                },
                "2": {"itemHash": ARMOUR_HASH, "costs": []},
            }
        },
        "itemComponents": {
            "stats": {"data": {"1": {"stats": {"20": {"statHash": 20, "value": 84}}}}},
            "perks": {"data": {"1": {"perks": [{"perkHash": 30}]}}},
        },
    }


@pytest_asyncio.fixture
async def lookup(tmp_path: Path) -> t.AsyncIterator[manifest_db.ManifestLookup]:
    version_id = await load_projection(DEFINITIONS, tmp_path)
    table = manifest_db.ManifestLookup(version_id)
    await table.preload_vendor_response(_vendor_response())
    yield table
    await clear_projection()


async def test_vendor_parses_from_the_projection(lookup) -> None:
    vendor = models.DestinyVendor.from_vendors_api_response(
        response=_vendor_response(), manifest_table=lookup
    )

    assert vendor.name == "Xûr"
    # vendorLocationIndex 1 -> the SECOND entry of `locations`, which is why the
    # projection keeps Bungie's list shape rather than a set of destination hashes.
    assert vendor.location == "Nessus"
    assert {item.name for item in vendor.sale_items} == {
        "Test Hand Cannon",
        "Test Helm",
    }


async def test_sale_item_carries_every_hydrated_field(lookup) -> None:
    vendor = models.DestinyVendor.from_vendors_api_response(
        response=_vendor_response(), manifest_table=lookup
    )
    weapon = next(i for i in vendor.sale_items if i.name == "Test Hand Cannon")

    assert weapon.is_weapon
    assert weapon.rarity == "Legendary"
    assert weapon.class_ == "Hunter"
    assert weapon.bucket == "Kinetic Weapons"
    assert weapon.item_type_friendly_name == "Hand Cannon"
    assert weapon.icon_path == "/hc.png"
    # Currency names come off the item table too — a projection restricted to weapons
    # and armour would have priced this at nothing.
    assert weapon.costs == {"Glimmer": 1000}
    # Resolved through the API response's hashes against the projection's name tables.
    assert weapon.stats == {"Impact": 84}
    assert weapon.perks == ["Rampage"]


async def test_collectible_set_resolves_through_its_presentation_node(lookup) -> None:
    """item -> collectibleHash -> parentNodeHashes[0] -> name, all three tables deep."""
    vendor = models.DestinyVendor.from_vendors_api_response(
        response=_vendor_response(), manifest_table=lookup
    )
    armour = next(i for i in vendor.sale_items if i.name == "Test Helm")
    assert armour.collectible_set_name == "Wild Hunt Suit"


async def test_an_item_without_a_collectible_has_no_set(lookup) -> None:
    """``from_sale_item`` branches on the *presence* of ``collectibleHash``.

    Hydrating it as an explicit ``None`` would send every item down the collectible
    path — so this is the test that a null column stays an absent key.
    """
    vendor = models.DestinyVendor.from_vendors_api_response(
        response=_vendor_response(), manifest_table=lookup
    )
    weapon = next(i for i in vendor.sale_items if i.name == "Test Hand Cannon")
    assert weapon.collectible_set_name is None


async def test_only_the_referenced_rows_are_loaded(lookup) -> None:
    """The lookup is scoped to one operation, not a copy of the manifest.

    Three items exist in the projection and three are referenced by this response
    (two on sale, one as a cost), but only the destinations and slots those point at
    come along — the second destination is loaded because the vendor names it, not
    because the table was copied wholesale.
    """
    assert set(lookup["DestinyInventoryItemDefinition"]) == {
        WEAPON_HASH,
        ARMOUR_HASH,
        GLIMMER_HASH,
    }
    assert set(lookup["DestinyEquipmentSlotDefinition"]) == {9001, 9002}
    assert set(lookup["DestinyPresentationNodeDefinition"]) == {600}


async def test_a_missing_hash_behaves_like_a_dict(lookup) -> None:
    """A Bungie hotfix shipping an item mid-week is a miss, not a crash.

    Same contract the sqlite-backed lookup had: ``KeyError`` on subscript, the default
    from ``.get`` — so every call site's existing degrade path still fires.
    """
    with pytest.raises(KeyError):
        lookup["DestinyInventoryItemDefinition"][123456]
    assert lookup["DestinyInventoryItemDefinition"].get(123456) is None
    assert (
        lookup["DestinyInventoryItemDefinition"].get(123456, "fallback") == "fallback"
    )


async def test_require_version_id_raises_without_a_manifest() -> None:
    await clear_projection()
    with pytest.raises(manifest_db.ManifestUnavailable):
        await manifest_db.require_version_id()
    assert await manifest_db.current_version_id() is None
