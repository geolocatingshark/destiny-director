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
