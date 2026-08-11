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

"""Autocomplete and light.gg resolution, against the manifest projection.

These used to inject a hand-built dict into a module global; the index is SQL now, so
they insert rows and query them. Same behaviours asserted either way — the collectible
and season preferences, the kind filter, the (name, type) dedupe, and degrading to
nothing when no manifest has been ingested.
"""

from __future__ import annotations

import datetime as dt
import typing as t

import pytest
import pytest_asyncio
from sqlalchemy import delete, insert

from dd.anchor.extensions.bungie_api import item_index
from dd.common import schemas

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _item(hash_: int, name: str, **overrides: t.Any) -> dict[str, t.Any]:
    row = {
        "hash": hash_,
        "name": name,
        "name_lower": name.lower(),
        "icon": "i",
        "description": "",
        "item_type": 3,
        "item_type_display_name": "Auto Rifle",
        "tier_type_name": "Legendary",
        "class_type": 3,
        "bucket_type_hash": None,
        "collectible_hash": None,
        "season_number": None,
        "trait_ids": [],
        "redacted": False,
        "raw": None,
    }
    return row | overrides


@pytest_asyncio.fixture
async def projection() -> t.AsyncIterator[t.Callable[..., t.Awaitable[int]]]:
    """Hand out a current manifest version populated with the given items."""
    async with schemas.db_session() as session:
        await session.execute(delete(schemas.ManifestItem))
        await session.execute(delete(schemas.ManifestVersion))
        await session.commit()

    async def populate(*items: dict[str, t.Any]) -> int:
        async with schemas.db_session() as session:
            version_id = int(
                (
                    await session.execute(
                        insert(schemas.ManifestVersion)
                        .values(
                            version="test",
                            ingested_at=dt.datetime.now(),
                            is_current=True,
                        )
                        .returning(schemas.ManifestVersion.id)
                    )
                ).scalar_one()
            )
            for item in items:
                session.add(schemas.ManifestItem(version_id=version_id, **item))
            await session.commit()
            return version_id

    yield populate

    async with schemas.db_session() as session:
        await session.execute(delete(schemas.ManifestItem))
        await session.execute(delete(schemas.ManifestVersion))
        await session.commit()


@pytest_asyncio.fixture
async def index(projection) -> None:
    await projection(
        _item(100, "Chroma Rush", collectible_hash=1),
        _item(50, "Chroma Rush"),
        _item(
            200,
            "Wild Hunt Vest",
            item_type=2,
            item_type_display_name="Hunter Armor",
            collectible_hash=2,
        ),
    )


async def test_resolve_prefers_collectible_type_match(index) -> None:
    # Two "Chroma Rush" entries; the collectible reissue (hash 100) wins.
    assert (
        await item_index.resolve_light_gg_url("Chroma Rush (Auto Rifle)")
        == "https://www.light.gg/db/items/100/"
    )


async def test_resolve_unknown_name_is_none(index) -> None:
    assert await item_index.resolve_light_gg_url("Nonexistent (Shotgun)") is None


async def test_resolve_prefers_newer_season_over_hash(projection) -> None:
    # Two collectible, type-matching "Recluse" copies: the reissue is a NEWER season but
    # a LOWER hash. Season number must win over the hash tiebreak.
    common = {"item_type_display_name": "Submachine Gun", "collectible_hash": 1}
    await projection(
        _item(900, "Recluse", season_number=6, **common),  # original
        _item(100, "Recluse", season_number=23, **common),  # reissue
    )
    assert (
        await item_index.resolve_light_gg_url("Recluse (Submachine Gun)")
        == "https://www.light.gg/db/items/100/"
    )


async def test_resolve_many_answers_every_value_in_one_query(index) -> None:
    """The save path bakes a whole document's links, so it is one query, not N."""
    resolved = await item_index.resolve_light_gg_urls(
        ["Chroma Rush (Auto Rifle)", "Wild Hunt Vest (Hunter Armor)", "Nope (Bow)"]
    )
    assert resolved == {
        "Chroma Rush (Auto Rifle)": "https://www.light.gg/db/items/100/",
        "Wild Hunt Vest (Hunter Armor)": "https://www.light.gg/db/items/200/",
    }


async def test_search_weapon_kind(index) -> None:
    res = await item_index.search("chroma", kind="weapon")
    assert res and res[0]["name"] == "Chroma Rush"
    assert res[0]["url"] == "https://www.light.gg/db/items/100/"
    # deduped by (name, type): only one Chroma Rush entry.
    assert len(res) == 1


async def test_search_kind_filter(index) -> None:
    assert await item_index.search("chroma", kind="armor") == []
    assert (await item_index.search("wild", kind="armor"))[0][
        "name"
    ] == "Wild Hunt Vest"


async def test_search_prefers_prefix_matches(projection) -> None:
    await projection(
        _item(1, "Rush Job"),  # prefix match
        _item(2, "Chroma Rush"),  # substring match only
    )
    assert [r["name"] for r in await item_index.search("rush")] == [
        "Rush Job",
        "Chroma Rush",
    ]


async def test_search_only_offers_weapons_and_armour(projection) -> None:
    """The item table holds currencies and cosmetics too; autocomplete must not."""
    await projection(
        _item(1, "Bright Dust", item_type=0, item_type_display_name=""),
        _item(2, "Bright Chroma"),
    )
    assert [r["name"] for r in await item_index.search("bright")] == ["Bright Chroma"]


async def test_search_treats_the_query_as_a_literal(projection) -> None:
    """``LIKE`` metacharacters must not leak out of the query string.

    The in-memory index used Python's ``in``, which has no pattern language; an
    unescaped ``%`` would turn an autocomplete keystroke into "match everything".
    """
    await projection(_item(1, "Chroma Rush"), _item(2, "100% Delicious"))

    # Unescaped, `%` is "match anything" and this returns both rows. Escaped, it is a
    # per cent sign, and only the item that actually contains one comes back.
    assert [r["name"] for r in await item_index.search("%")] == ["100% Delicious"]
    assert [r["name"] for r in await item_index.search("100%")] == ["100% Delicious"]
    # `_` is LIKE's single-character wildcard; it must not match the space here.
    assert await item_index.search("chroma_rush") == []


async def test_no_ingested_manifest_degrades_gracefully(projection) -> None:
    assert not await item_index.ready()
    assert await item_index.search("anything") == []
    assert await item_index.resolve_light_gg_url("Chroma Rush (Auto Rifle)") is None
    assert await item_index.resolve_light_gg_urls(["Chroma Rush (Auto Rifle)"]) == {}


async def test_a_superseded_version_is_not_read(projection) -> None:
    """Readers pin the current version; rows from an older one must stay invisible."""
    await projection(_item(100, "Chroma Rush"))
    async with schemas.db_session() as session:
        old_id = (
            await session.execute(
                insert(schemas.ManifestVersion)
                .values(version="old", ingested_at=dt.datetime.now(), is_current=False)
                .returning(schemas.ManifestVersion.id)
            )
        ).scalar_one()
        session.add(
            schemas.ManifestItem(version_id=old_id, **_item(999, "Gjallarhorn"))
        )
        await session.commit()

    assert await item_index.search("gjallarhorn") == []
    assert [r["name"] for r in await item_index.search("chroma")] == ["Chroma Rush"]
