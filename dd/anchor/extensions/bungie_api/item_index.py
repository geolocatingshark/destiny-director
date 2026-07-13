"""A name → item index over the Destiny manifest, for the rotation editor.

Powers weapon/armor name autocomplete and light.gg link resolution. Built once from the
cached manifest SQLite (weapon + armor items only) and held in memory; consumers read it
synchronously. Everything degrades gracefully when the index isn't warm yet or no Bungie
API key is configured — autocomplete returns nothing and link resolution returns
``None`` rather than blocking a request on the (large, slow) manifest download.
"""

import asyncio
import json
import logging
import sqlite3
import typing as t

from .constants import DESTINY_ITEM_TYPE_ARMOR, DESTINY_ITEM_TYPE_WEAPON
from .manifest import _get_latest_manifest

logger = logging.getLogger(__name__)

LIGHT_GG_URL = "https://www.light.gg/db/items/{}/"

# Built index: name.lower() -> list of entries. Only the latest manifest is kept.
_index: dict[str, list[dict[str, t.Any]]] | None = None
_index_path: str | None = None
_build_lock = asyncio.Lock()


def _plain_name(value: str) -> str:
    """The bare item name from a stored value like ``Chroma Rush (Auto Rifle)``."""
    return value.split(" (")[0].strip()


def _build_sync(path: str) -> dict[str, list[dict[str, t.Any]]]:
    """Parse the manifest item table into a name index (runs in a worker thread)."""
    index: dict[str, list[dict[str, t.Any]]] = {}
    con = sqlite3.connect(path)
    try:
        rows = con.execute("SELECT json FROM DestinyInventoryItemDefinition")
        for (raw,) in rows:
            item = json.loads(raw)
            item_type = item.get("itemType")
            if item_type not in (DESTINY_ITEM_TYPE_WEAPON, DESTINY_ITEM_TYPE_ARMOR):
                continue
            display = item.get("displayProperties") or {}
            name = (display.get("name") or "").strip()
            if not name:
                continue
            index.setdefault(name.lower(), []).append(
                {
                    "name": name,
                    "hash": item["hash"],
                    "type": item.get("itemTypeDisplayName", ""),
                    "item_type": item_type,
                    "icon": display.get("icon", ""),
                    "collectible": bool(item.get("collectibleHash")),
                }
            )
    finally:
        con.close()
    return index


async def warm(api_key: str) -> None:
    """Build (or rebuild on a manifest update) the index. Safe to call repeatedly.

    Downloads/caches the manifest if needed (slow, once) then parses it in a thread. A
    no-op without an API key. Call from a background task so requests never block on it.
    """
    global _index, _index_path
    if not api_key:
        return
    async with _build_lock:
        try:
            path = await _get_latest_manifest(api_key)
        except Exception:
            logger.exception("item_index: could not fetch the manifest")
            return
        if path == _index_path and _index is not None:
            return
        index = await asyncio.get_event_loop().run_in_executor(None, _build_sync, path)
        _index, _index_path = index, path
        logger.info("item_index: built (%d names)", len(index))


def ready() -> bool:
    return _index is not None


def resolve_light_gg_url(value: str) -> str | None:
    """Best-effort light.gg URL for a weapon value (``Name (Type)``), or ``None``.

    Prefers a weapon whose type matches the ``(Type)`` hint and that is collectible
    (in-game obtainable), then the newest (highest hash) reissue."""
    if _index is None:
        return None
    name = _plain_name(value)
    entries = _index.get(name.lower())
    if not entries:
        return None
    type_hint = value[len(name) :].strip(" ()").lower()

    def score(entry: dict[str, t.Any]) -> tuple:
        entry_type = (entry["type"] or "").lower()
        return (
            entry["item_type"] == DESTINY_ITEM_TYPE_WEAPON,
            bool(entry_type) and type_hint.startswith(entry_type),
            entry["collectible"],
            entry["hash"],
        )

    return LIGHT_GG_URL.format(max(entries, key=score)["hash"])


def search(
    query: str, kind: str | None = None, limit: int = 20
) -> list[dict[str, t.Any]]:
    """Name-substring search for autocomplete. ``kind`` filters to ``weapon``/``armor``.

    Returns ``{name, type, hash, url, icon}`` dicts, prefix matches and collectibles
    first, deduped by (name, type)."""
    if _index is None:
        return []
    q = query.lower().strip()
    if not q:
        return []
    want = {"weapon": DESTINY_ITEM_TYPE_WEAPON, "armor": DESTINY_ITEM_TYPE_ARMOR}.get(
        kind
    )

    matches: list[dict[str, t.Any]] = []
    for name_lower, entries in _index.items():
        if q not in name_lower:
            continue
        for entry in entries:
            if want is not None and entry["item_type"] != want:
                continue
            matches.append(entry)

    matches.sort(
        key=lambda e: (
            not e["name"].lower().startswith(q),
            not e["collectible"],
            e["name"],
        )
    )

    seen: set[tuple[str, str]] = set()
    results: list[dict[str, t.Any]] = []
    for entry in matches:
        key = (entry["name"], entry["type"])
        if key in seen:
            continue
        seen.add(key)
        results.append(
            {
                "name": entry["name"],
                "type": entry["type"],
                "hash": entry["hash"],
                "url": LIGHT_GG_URL.format(entry["hash"]),
                "icon": entry["icon"],
            }
        )
        if len(results) >= limit:
            break
    return results
