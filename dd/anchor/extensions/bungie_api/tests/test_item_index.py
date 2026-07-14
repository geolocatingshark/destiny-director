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

# Pure search/resolution logic over an injected in-memory index (no manifest download).

import json
import sqlite3

import pytest

from dd.anchor.extensions.bungie_api import item_index


@pytest.fixture
def index():
    item_index._index = {
        "chroma rush": [
            {
                "name": "Chroma Rush",
                "hash": 100,
                "type": "Auto Rifle",
                "item_type": 3,
                "icon": "i",
                "collectible": True,
            },
            {
                "name": "Chroma Rush",
                "hash": 50,
                "type": "Auto Rifle",
                "item_type": 3,
                "icon": "i",
                "collectible": False,
            },
        ],
        "wild hunt vest": [
            {
                "name": "Wild Hunt Vest",
                "hash": 200,
                "type": "Hunter Armor",
                "item_type": 2,
                "icon": "i",
                "collectible": True,
            },
        ],
    }
    item_index._index_path = "test"
    yield
    item_index._index = None
    item_index._index_path = None


def test_resolve_prefers_collectible_type_match(index):
    # Two "Chroma Rush" entries; the collectible reissue (hash 100) wins.
    assert (
        item_index.resolve_light_gg_url("Chroma Rush (Auto Rifle)")
        == "https://www.light.gg/db/items/100/"
    )


def test_resolve_unknown_name_is_none(index):
    assert item_index.resolve_light_gg_url("Nonexistent (Shotgun)") is None


def test_resolve_prefers_newer_season_over_hash():
    # Two collectible, type-matching "Recluse" copies: the reissue is a NEWER season but
    # a LOWER hash. Season number must win over the hash tiebreak (review finding #10).
    common = {"name": "Recluse", "type": "Submachine Gun", "item_type": 3, "icon": "i"}
    item_index._index = {
        "recluse": [
            {**common, "hash": 900, "collectible": True, "season": 6},  # original
            {**common, "hash": 100, "collectible": True, "season": 23},  # reissue
        ],
    }
    item_index._index_path = "test"
    try:
        assert (
            item_index.resolve_light_gg_url("Recluse (Submachine Gun)")
            == "https://www.light.gg/db/items/100/"
        )
    finally:
        item_index._index = None
        item_index._index_path = None


def test_search_weapon_kind(index):
    res = item_index.search("chroma", kind="weapon")
    assert res and res[0]["name"] == "Chroma Rush"
    assert res[0]["url"] == "https://www.light.gg/db/items/100/"
    # deduped by (name, type): only one Chroma Rush entry.
    assert len(res) == 1


def test_search_kind_filter(index):
    assert item_index.search("chroma", kind="armor") == []
    assert item_index.search("wild", kind="armor")[0]["name"] == "Wild Hunt Vest"


def test_cold_index_degrades_gracefully():
    item_index._index = None
    assert not item_index.ready()
    assert item_index.search("anything") == []
    assert item_index.resolve_light_gg_url("Chroma Rush (Auto Rifle)") is None


def _make_manifest(tmp_path, rows: list[str], seasons: list[str] | None = None) -> str:
    """A throwaway manifest SQLite whose item (and optional season) table hold the given
    raw json blobs."""
    path = str(tmp_path / "manifest.sqlite")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE DestinyInventoryItemDefinition (json TEXT)")
    con.executemany(
        "INSERT INTO DestinyInventoryItemDefinition (json) VALUES (?)",
        [(r,) for r in rows],
    )
    if seasons is not None:
        con.execute("CREATE TABLE DestinySeasonDefinition (json TEXT)")
        con.executemany(
            "INSERT INTO DestinySeasonDefinition (json) VALUES (?)",
            [(s,) for s in seasons],
        )
    con.commit()
    con.close()
    return path


def test_build_sync_joins_season_numbers(tmp_path):
    # An item's seasonHash resolves to the season's seasonNumber (review finding #10);
    # an item with no seasonHash falls back to -1.
    weapon = json.dumps(
        {
            "hash": 100,
            "seasonHash": 555,
            "itemType": 3,
            "itemTypeDisplayName": "Hand Cannon",
            "displayProperties": {"name": "Fatebringer", "icon": "i"},
        }
    )
    seasonless = json.dumps(
        {
            "hash": 200,
            "itemType": 3,
            "itemTypeDisplayName": "Scout Rifle",
            "displayProperties": {"name": "Jade Rabbit", "icon": "i"},
        }
    )
    season = json.dumps({"hash": 555, "seasonNumber": 15})
    path = _make_manifest(tmp_path, [weapon, seasonless], seasons=[season])

    index = item_index._build_sync(path)

    assert index["fatebringer"][0]["season"] == 15
    assert index["jade rabbit"][0]["season"] == -1  # no seasonHash → sentinel


def test_build_sync_skips_malformed_rows(tmp_path):
    # One good weapon, then a truncated-JSON row and a row missing "hash": the bad rows
    # must be skipped, not abort the whole build (review finding #9).
    good = json.dumps(
        {
            "hash": 100,
            "itemType": 3,
            "itemTypeDisplayName": "Auto Rifle",
            "displayProperties": {"name": "Chroma Rush", "icon": "i"},
            "collectibleHash": 1,
        }
    )
    missing_hash = json.dumps(
        {
            "itemType": 3,
            "displayProperties": {"name": "No Hash"},
        }
    )
    also_good = json.dumps(
        {
            "hash": 200,
            "itemType": 2,
            "itemTypeDisplayName": "Hunter Armor",
            "displayProperties": {"name": "Wild Hunt Vest", "icon": "i"},
        }
    )
    path = _make_manifest(
        tmp_path, [good, "{ this is not json", missing_hash, also_good]
    )

    index = item_index._build_sync(path)

    assert index["chroma rush"][0]["hash"] == 100
    assert index["wild hunt vest"][0]["hash"] == 200
    assert "no hash" not in index  # the hash-less row was skipped, not indexed
