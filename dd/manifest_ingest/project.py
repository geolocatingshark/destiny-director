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

"""Bungie's world-content sqlite → typed projection rows.

One builder per projected table, each turning a parsed manifest definition into the
column dict its model wants, plus :func:`iter_batches` to stream the whole thing.

**Streaming is the point.** The item table alone is ~39k rows and materialising its raw
JSON strings at once cost 240 MB before a single one was parsed — the reason the code
this replaces read in ``fetchmany`` batches. Here the whole write is one transaction, so
batches can be handed to the database as they are built and only one is ever alive.

Reads here are **synchronous** sqlite. That is deliberate and safe in this process and
nowhere else: the ingest is a single-purpose job with no gateway, no web server and no
concurrent request to starve, so interleaving blocking reads with the async writes that
consume them costs nothing and avoids a queue between two threads. Do not lift this
module into a bot.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import typing as t
from collections.abc import Iterator

from dd.common import schemas

logger = logging.getLogger(__name__)

#: Rows handed to the database per statement. Sized to keep one batch's parsed JSON
#: (worst case the item table, whose rows carry a ``raw`` copy) comfortably small while
#: still amortising the round-trip.
BATCH_SIZE = 500

#: Bungie item types whose full JSON is kept in ``ManifestItem.raw``. Every item gets a
#: projected *row* — the Eververse post reads currencies and cosmetics, which are
#: neither — but ``raw`` is the whole definition and would dominate the table's disk if
#: it were kept for all ~39k of them. Weapons and armour are where a new field would
#: plausibly be prototyped, so they are the ones worth the space.
_RAW_ITEM_TYPES = frozenset({2, 3})


def _display(defn: dict[str, t.Any]) -> dict[str, t.Any]:
    """``displayProperties``, tolerating the ``null`` Bungie sometimes ships."""
    return defn.get("displayProperties") or {}


def _name(defn: dict[str, t.Any]) -> str:
    return (_display(defn).get("name") or "").strip()


def _item_row(defn: dict[str, t.Any], seasons: dict[int, int]) -> dict[str, t.Any]:
    display = _display(defn)
    name = (display.get("name") or "").strip()
    inventory = defn.get("inventory") or {}
    item_type = int(defn.get("itemType") or 0)
    return {
        "hash": int(defn["hash"]),
        "name": name,
        # Casefolded once here so every consumer's name match is an indexed comparison
        # instead of a per-row lower() the planner cannot use an index for.
        "name_lower": name.lower(),
        "icon": display.get("icon") or "",
        "description": display.get("description") or "",
        "item_type": item_type,
        "item_type_display_name": defn.get("itemTypeDisplayName") or "",
        "tier_type_name": inventory.get("tierTypeName") or "",
        "class_type": int(defn.get("classType") or 0),
        "bucket_type_hash": inventory.get("bucketTypeHash"),
        "collectible_hash": defn.get("collectibleHash"),
        # Resolved here rather than stored as a seasonHash: the number is the only
        # thing read (it is the authoritative recency key for a reissued weapon), and
        # resolving it at ingest saves every reader a join against a 30-row table.
        "season_number": seasons.get(defn.get("seasonHash")),
        "trait_ids": defn.get("traitIds") or [],
        "redacted": bool(defn.get("redacted")),
        "raw": defn if item_type in _RAW_ITEM_TYPES else None,
    }


def _named_row(defn: dict[str, t.Any]) -> dict[str, t.Any]:
    """The shared shape of every table that projects to nothing but a name."""
    return {"hash": int(defn["hash"]), "name": _name(defn)}


def _perk_row(defn: dict[str, t.Any]) -> dict[str, t.Any]:
    return _named_row(defn) | {"icon": _display(defn).get("icon") or ""}


def _collectible_row(defn: dict[str, t.Any]) -> dict[str, t.Any]:
    return _named_row(defn) | {
        "description": _display(defn).get("description") or "",
        "item_hash": defn.get("itemHash"),
        "parent_node_hashes": defn.get("parentNodeHashes") or [],
    }


def _vendor_row(defn: dict[str, t.Any]) -> dict[str, t.Any]:
    return _named_row(defn) | {
        "vendor_identifier": defn.get("vendorIdentifier") or "",
        # Bungie's list shape is kept because the vendor API answers with an *index
        # into it*: reordering or reshaping it would silently relocate every vendor.
        "locations": defn.get("locations") or [],
    }


def _activity_row(defn: dict[str, t.Any]) -> dict[str, t.Any]:
    return _named_row(defn) | {
        "activity_type_hash": defn.get("activityTypeHash"),
        "mode_types": defn.get("activityModeTypes") or [],
        "direct_mode_type": defn.get("directActivityModeType"),
        "max_party": (defn.get("matchmaking") or {}).get("maxParty"),
    }


#: ``(model, manifest table, row builder)`` for everything projected. This IS the
#: allowlist — a manifest table absent from here is never read, and adding one means
#: adding a model and a migration alongside it.
_PROJECTION: tuple[tuple[type[t.Any], str, t.Callable[..., dict[str, t.Any]]], ...] = (
    (schemas.ManifestItem, "DestinyInventoryItemDefinition", _item_row),
    (schemas.ManifestPerk, "DestinySandboxPerkDefinition", _perk_row),
    (schemas.ManifestStat, "DestinyStatDefinition", _named_row),
    (schemas.ManifestEquipmentSlot, "DestinyEquipmentSlotDefinition", _named_row),
    (schemas.ManifestDestination, "DestinyDestinationDefinition", _named_row),
    (schemas.ManifestPresentationNode, "DestinyPresentationNodeDefinition", _named_row),
    (schemas.ManifestCollectible, "DestinyCollectibleDefinition", _collectible_row),
    (schemas.ManifestVendor, "DestinyVendorDefinition", _vendor_row),
    (schemas.ManifestActivityType, "DestinyActivityTypeDefinition", _named_row),
    (schemas.ManifestActivity, "DestinyActivityDefinition", _activity_row),
)


def _iter_definitions(
    con: sqlite3.Connection, table: str
) -> Iterator[dict[str, t.Any]]:
    """Parsed definitions from one manifest table, skipping unreadable rows.

    A table Bungie has dropped, or an individually-malformed row, degrades to "fewer
    rows" rather than taking the whole ingest down: the transaction that follows is
    all-or-nothing, so a single bad blob aborting it would mean the projection never
    advances again until Bungie fixed it.
    """
    try:
        cursor = con.execute(f'SELECT json FROM "{table}"')  # noqa: S608 - fixed names
    except sqlite3.Error:
        logger.warning("Manifest table %s is missing; projecting none of it", table)
        return
    while batch := cursor.fetchmany(BATCH_SIZE):
        for (raw,) in batch:
            try:
                defn = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if defn.get("hash") is None:
                continue
            yield defn


def season_numbers(con: sqlite3.Connection) -> dict[int, int]:
    """``seasonHash -> seasonNumber``, or ``{}`` when the table is unavailable.

    Small enough (a few dozen rows) to hold whole, and needed before the item walk
    starts so each item can carry its resolved number.
    """
    numbers: dict[int, int] = {}
    for defn in _iter_definitions(con, "DestinySeasonDefinition"):
        number = defn.get("seasonNumber")
        if number is not None:
            numbers[int(defn["hash"])] = int(number)
    return numbers


def iter_batches(
    path: str, version_id: int
) -> Iterator[tuple[type[t.Any], list[dict[str, t.Any]]]]:
    """Stream ``(model, rows)`` batches for every projected table.

    Each row already carries ``version_id``, so the caller inserts what it is handed
    without knowing the shape of any table.
    """
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        seasons = season_numbers(con)
        for model, table, build in _PROJECTION:
            count = 0
            batch: list[dict[str, t.Any]] = []
            for defn in _iter_definitions(con, table):
                row = (
                    build(defn, seasons)
                    if model is schemas.ManifestItem
                    else build(defn)
                )
                batch.append(row | {"version_id": version_id})
                if len(batch) >= BATCH_SIZE:
                    count += len(batch)
                    yield model, batch
                    batch = []
            if batch:
                count += len(batch)
                yield model, batch
            logger.info("Projected %s: %d rows", table, count)
    finally:
        con.close()
