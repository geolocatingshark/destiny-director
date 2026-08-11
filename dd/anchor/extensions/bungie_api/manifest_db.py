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

"""Reading the manifest projection: pinning a version, and escaping SQL patterns.

The projection is written by ``dd.manifest_ingest`` (a cron service, out of process) and
read from here. The one rule every reader follows:

    **Resolve the current version once per operation, then pin it.**

An "operation" is one post construction or one HTTP request. Pin it and a manifest flip
landing mid-operation cannot mix two seasons inside a single post; re-resolve it per
query and it can. There is no cache to invalidate and no warm-up to wait for — before
the first ingest :func:`current_version_id` is simply ``None`` and every consumer
degrades exactly as it already did for a cold index.
"""

from __future__ import annotations

import logging
import typing as t

from dd.common import schemas

logger = logging.getLogger(__name__)

#: The character :func:`like_escape` escapes with. Backslash is SQL's conventional
#: choice and is spelled out in each ``LIKE`` via ``ESCAPE``, because Postgres and
#: SQLite disagree about what the default is when you leave it out.
LIKE_ESCAPE = "\\"


async def current_version_id() -> int | None:
    """The current manifest version's id, or ``None`` if nothing has been ingested.

    Deliberately not memoised. It is one indexed single-row read, and a TTL cache would
    buy microseconds in exchange for a window in which two processes disagree about
    which manifest is current — the thing the version pinning exists to rule out.
    """
    return await schemas.ManifestVersion.current_id()


def like_escape(value: str) -> str:
    """Neutralise ``LIKE``'s wildcards in a user-supplied substring.

    The searches this feeds replaced Python's ``in``/``startswith``, which have no
    pattern language at all. Passing a raw query into ``LIKE`` would quietly hand the
    user two metacharacters: ``%`` (matching everything, so an autocomplete of "%"
    returns arbitrary items) and ``_`` (matching any character, which real item names
    contain). Escaping them keeps the SQL a literal substring test, which is what the
    call sites mean and what they used to do.
    """
    for char in (LIKE_ESCAPE, "%", "_"):
        value = value.replace(char, LIKE_ESCAPE + char)
    return value


class ManifestUnavailable(Exception):
    """No manifest has been ingested yet, so a post that needs one cannot be built."""


async def require_version_id() -> int:
    """The current version id, or raise.

    For the callers that genuinely cannot proceed — a Xûr or Eververse post is the
    manifest, not decorated by it. They used to fail at the download instead; failing
    here is the same outcome with a better message, and the autopost machinery already
    treats a raising producer as "skip this run and alert".
    """
    version_id = await current_version_id()
    if version_id is None:
        raise ManifestUnavailable(
            "no Destiny manifest has been ingested yet (see dd.manifest_ingest)"
        )
    return version_id


# --- hydration ------------------------------------------------------------------------
#
# The projection stores typed columns; :mod:`.models` parses Bungie's JSON. Rather
# than rewrite those parsers — they are the shared definition of what a Destiny item
# *is*, and the post golden tests exercise them — the rows are rebuilt into the JSON
# shape the parsers already read. The mapping is small, mechanical, and lives here.
#
# The one subtlety: keys that are *absent* in Bungie's JSON must be absent here too.
# ``DestinyItem.from_sale_item`` branches on ``"collectibleHash" in manifest_entry``, so
# a hydrated ``{"collectibleHash": None}`` would send every item down the collectible
# path and fail resolving it.

#: Table names the lookup answers for, mirroring ``constants.manifest_table_names``.
_TABLES = (
    "DestinyInventoryItemDefinition",
    "DestinySandboxPerkDefinition",
    "DestinyStatDefinition",
    "DestinyEquipmentSlotDefinition",
    "DestinyCollectibleDefinition",
    "DestinyDestinationDefinition",
    "DestinyPresentationNodeDefinition",
    "DestinyVendorDefinition",
)


def _display(name: str, **extra: t.Any) -> dict[str, t.Any]:
    return {"name": name, **{k: v for k, v in extra.items() if v is not None}}


def _item_json(row: t.Any) -> dict[str, t.Any]:
    entry: dict[str, t.Any] = {
        "hash": int(row.hash),
        "displayProperties": _display(
            row.name, icon=row.icon or None, description=row.description or None
        ),
        "itemType": int(row.item_type),
        "itemTypeDisplayName": row.item_type_display_name,
        "classType": int(row.class_type),
        "inventory": {"tierTypeName": row.tier_type_name},
        "traitIds": list(row.trait_ids or []),
    }
    if row.bucket_type_hash is not None:
        entry["inventory"]["bucketTypeHash"] = int(row.bucket_type_hash)
    # Presence, not value — see the note above.
    if row.collectible_hash:
        entry["collectibleHash"] = int(row.collectible_hash)
    return entry


def _named_json(row: t.Any) -> dict[str, t.Any]:
    return {"hash": int(row.hash), "displayProperties": _display(row.name)}


def _perk_json(row: t.Any) -> dict[str, t.Any]:
    return {
        "hash": int(row.hash),
        "displayProperties": _display(row.name, icon=row.icon or None),
    }


def _collectible_json(row: t.Any) -> dict[str, t.Any]:
    return {
        "hash": int(row.hash),
        "displayProperties": _display(row.name, description=row.description or None),
        "itemHash": row.item_hash,
        "parentNodeHashes": list(row.parent_node_hashes or []),
    }


def _vendor_json(row: t.Any) -> dict[str, t.Any]:
    return {
        "hash": int(row.hash),
        "displayProperties": _display(row.name),
        "vendorIdentifier": row.vendor_identifier,
        "locations": list(row.locations or []),
    }


class ManifestLookup(dict[str, t.Any]):
    """``{table: {hash: manifest-json}}`` for exactly what one operation touches.

    A drop-in for the sqlite-backed lookup it replaces — the consumers' annotations and
    every ``manifest_table["Destiny…Definition"][hash]`` call site are unchanged, and
    each per-table value is a plain dict, so a missing hash raises ``KeyError`` and
    ``.get`` returns its default exactly as before.

    What changed is *when* rows arrive. The sqlite version read a row per lookup,
    from a file this process had downloaded; this one is filled by
    :meth:`preload_vendor_response` in three bounded round-trips before any parsing
    starts, because the parsers are synchronous and the database is not. Three rather
    than one because the reference graph has depth: items name their bucket and
    collectible, a collectible names its presentation nodes, so each round loads what
    the previous round's rows pointed at.

    ``version_id`` is pinned for the lookup's whole life — that is what stops a manifest
    flip landing mid-post and mixing two seasons into one message.
    """

    def __init__(self, version_id: int) -> None:
        super().__init__({name: {} for name in _TABLES})
        self.version_id = version_id

    def _merge(self, table: str, rows: dict[int, t.Any], build: t.Any) -> None:
        self[table].update({hash_: build(row) for hash_, row in rows.items()})

    async def preload_items(self, hashes: t.Iterable[int]) -> None:
        """Load these items and everything they point at (buckets, collectibles, sets).

        Public because the Eververse post preloads the items it has already collected
        across several vendor calls, deduped, rather than per response.
        """
        items = await schemas.ManifestItem.by_hashes(self.version_id, hashes)
        self._merge("DestinyInventoryItemDefinition", items, _item_json)

        buckets = {
            int(row.bucket_type_hash)
            for row in items.values()
            if row.bucket_type_hash is not None
        }
        collectibles = {
            int(row.collectible_hash) for row in items.values() if row.collectible_hash
        }
        self._merge(
            "DestinyEquipmentSlotDefinition",
            await schemas.ManifestEquipmentSlot.by_hashes(self.version_id, buckets),
            _named_json,
        )
        collectible_rows = await schemas.ManifestCollectible.by_hashes(
            self.version_id, collectibles
        )
        self._merge("DestinyCollectibleDefinition", collectible_rows, _collectible_json)

        nodes = {
            int(node)
            for row in collectible_rows.values()
            for node in (row.parent_node_hashes or [])
        }
        self._merge(
            "DestinyPresentationNodeDefinition",
            await schemas.ManifestPresentationNode.by_hashes(self.version_id, nodes),
            _named_json,
        )

    async def preload_vendor_response(self, response: dict[str, t.Any]) -> None:
        """Load everything one ``fetch_vendor`` response will be parsed against."""
        vendor_hash = response["vendor"]["data"]["vendorHash"]
        vendors = await schemas.ManifestVendor.by_hashes(self.version_id, [vendor_hash])
        self._merge("DestinyVendorDefinition", vendors, _vendor_json)

        destinations = {
            int(location["destinationHash"])
            for row in vendors.values()
            for location in (row.locations or [])
            if location.get("destinationHash") is not None
        }
        self._merge(
            "DestinyDestinationDefinition",
            await schemas.ManifestDestination.by_hashes(self.version_id, destinations),
            _named_json,
        )

        sales: dict[str, t.Any] = response.get("sales", {}).get("data", {}) or {}
        item_hashes = {int(sale["itemHash"]) for sale in sales.values()}
        # Cost currencies (Bright Dust, Silver, …) are items too, and are looked up by
        # name off the same table. They are the reason the projection covers every item
        # rather than just weapons and armour.
        item_hashes |= {
            int(cost["itemHash"])
            for sale in sales.values()
            for cost in (sale.get("costs") or [])
            if cost.get("itemHash")
        }
        await self.preload_items(item_hashes)

        components: dict[str, t.Any] = response.get("itemComponents", {}) or {}
        stat_hashes = {
            int(stat["statHash"])
            for entry in (components.get("stats", {}).get("data", {}) or {}).values()
            for stat in (entry.get("stats", entry) or {}).values()
        }
        perk_hashes = {
            int(perk["perkHash"])
            for entry in (components.get("perks", {}).get("data", {}) or {}).values()
            for perk in (entry.get("perks", entry) or [])
        }
        self._merge(
            "DestinyStatDefinition",
            await schemas.ManifestStat.by_hashes(self.version_id, stat_hashes),
            _named_json,
        )
        self._merge(
            "DestinySandboxPerkDefinition",
            await schemas.ManifestPerk.by_hashes(self.version_id, perk_hashes),
            _perk_json,
        )
