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

"""A miniature world-content sqlite, shaped exactly like Bungie's.

Same two-column ``(id INTEGER PRIMARY KEY, json)`` layout, and the ids are stored
**signed** — the wrap Bungie's own sqlite applies to hashes at or above ``2**31``. The
projection reads the ``json`` column and never the id, which is what lets the whole
``_to_signed_id`` dance die with the sqlite; a fixture that stored ids unsigned would
not prove that.
"""

from __future__ import annotations

import json
import sqlite3
import typing as t
from pathlib import Path

#: Above 2**31, so its stored id is negative. The projection must still come back with
#: the unsigned value, because that is what every consumer's hashes are.
BIG_HASH = 3_000_000_000

SEASON_HASH = 900
SEASON_NUMBER = 26


def _signed(hash_: int) -> int:
    return hash_ - 2**32 if hash_ >= 2**31 else hash_


#: ``table -> definitions``. Deliberately small but covering each shape the projection
#: has a branch for: a weapon and armour piece (which get ``raw``), a currency and an
#: ornament (which do not, but must still be projected), an unnamed row, a redacted row.
DEFINITIONS: dict[str, list[dict[str, t.Any]]] = {
    "DestinySeasonDefinition": [
        {"hash": SEASON_HASH, "seasonNumber": SEASON_NUMBER},
        {"hash": 901},  # no seasonNumber — must not land in the mapping
    ],
    "DestinyInventoryItemDefinition": [
        {
            "hash": 1,
            "displayProperties": {"name": "Chroma Rush", "icon": "/chroma.png"},
            "itemType": 3,
            "itemTypeDisplayName": "Auto Rifle",
            "inventory": {"tierTypeName": "Legendary", "bucketTypeHash": 700},
            "classType": 3,
            "collectibleHash": 500,
            "seasonHash": SEASON_HASH,
            "traitIds": ["weapon.auto_rifle"],
        },
        {
            "hash": 2,
            "displayProperties": {"name": "Wild Hunt Vest", "icon": "/vest.png"},
            "itemType": 2,
            "itemTypeDisplayName": "Chest Armor",
            "inventory": {"tierTypeName": "Legendary", "bucketTypeHash": 701},
            "classType": 1,
        },
        {
            "hash": 3,
            "displayProperties": {"name": "Bright Dust", "icon": "/dust.png"},
            "itemType": 0,  # a currency: not a weapon or armour, still needed by costs
            "inventory": {"tierTypeName": "Common"},
        },
        {
            "hash": BIG_HASH,
            "displayProperties": {
                "name": "Vestian Dynasty",
                "description": "...change the appearance of Hawkmoon.",
            },
            "itemType": 19,  # an ornament: read for traitIds + description
            "itemTypeDisplayName": "Weapon Ornament",
            "traitIds": ["item.ornament.weapon"],
            "inventory": {"tierTypeName": "Exotic"},
        },
        {"hash": 5, "itemType": 3, "redacted": True},  # unnamed + redacted
    ],
    "DestinySandboxPerkDefinition": [
        {"hash": 10, "displayProperties": {"name": "Rampage", "icon": "/perk.png"}}
    ],
    "DestinyStatDefinition": [{"hash": 20, "displayProperties": {"name": "Impact"}}],
    "DestinyEquipmentSlotDefinition": [
        {"hash": 700, "displayProperties": {"name": "Kinetic Weapons"}},
        {"hash": 701, "displayProperties": {"name": "Chest Armor"}},
    ],
    "DestinyDestinationDefinition": [
        {"hash": 800, "displayProperties": {"name": "The Tower"}}
    ],
    "DestinyPresentationNodeDefinition": [
        {"hash": 600, "displayProperties": {"name": "Wild Hunt Suit"}}
    ],
    "DestinyCollectibleDefinition": [
        {
            "hash": 500,
            "displayProperties": {"name": "Chroma Rush", "description": "An auto."},
            "itemHash": 1,
            "parentNodeHashes": [600],
        }
    ],
    "DestinyVendorDefinition": [
        {
            "hash": 2190858386,
            "displayProperties": {"name": "Xûr"},
            "vendorIdentifier": "XUR",
            "locations": [{"destinationHash": 800}],
        },
        {
            "hash": 111,
            "displayProperties": {"name": "Rotator"},
            "vendorIdentifier": "EVERVERSE_BRIGHT_DUST_ROTATOR_GHOSTS",
        },
        {
            "hash": 112,
            "displayProperties": {"name": "Other"},
            "vendorIdentifier": "EVERVERSE_SILVER_ROTATOR_GHOSTS",
        },
    ],
    "DestinyActivityTypeDefinition": [
        {"hash": 300, "displayProperties": {"name": "Strike"}}
    ],
    "DestinyActivityDefinition": [
        {
            "hash": 400,
            "displayProperties": {"name": "The Corrupted"},
            "activityTypeHash": 300,
            "activityModeTypes": [18],
            "directActivityModeType": 18,
            "matchmaking": {"maxParty": 3},
        }
    ],
}


def write_manifest(
    path: Path, definitions: dict[str, list[dict[str, t.Any]]] | None = None
) -> Path:
    """Write ``definitions`` (default :data:`DEFINITIONS`) as a manifest sqlite."""
    con = sqlite3.connect(path)
    try:
        for table, rows in (definitions or DEFINITIONS).items():
            con.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY, json)')
            con.executemany(
                f'INSERT INTO "{table}" (id, json) VALUES (?, ?)',  # noqa: S608
                [(_signed(int(row["hash"])), json.dumps(row)) for row in rows],
            )
        con.commit()
    finally:
        con.close()
    return path
