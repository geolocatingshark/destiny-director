"""A name → item index over the Destiny manifest, for the rotation editor.

Powers weapon/armor name autocomplete and light.gg link resolution. Both are now SQL
queries against the manifest projection (:mod:`.manifest_db`) rather than reads of an
in-memory dict: there is no index to build, no ``warm`` to call, and no window after a
boot in which autocomplete silently returns nothing.

What the in-memory index cost was not the dict — it was building it. Every process that
wanted a name lookup had to download the manifest and parse the whole ~39k-row item
table to get one, which was anchor's memory peak and the reason this module had a
background warm task and a "not ready yet" state at all. The ingest does that walk now,
in a process that exits.

Everything still degrades gracefully: before the first ingest there is no current
version, so autocomplete returns nothing and link resolution returns ``None`` rather
than raising.
"""

import logging
import typing as t

from sqlalchemy import case, select

from dd.common import schemas

from .constants import DESTINY_ITEM_TYPE_ARMOR, DESTINY_ITEM_TYPE_WEAPON
from .manifest_db import LIKE_ESCAPE, current_version_id, like_escape

logger = logging.getLogger(__name__)

LIGHT_GG_URL = "https://www.light.gg/db/items/{}/"

#: Rows fetched per requested autocomplete result before deduplication. The query
#: orders exactly as the returned list does, so this is a window on an already-correct
#: ranking rather than a filter — but it *is* a cap, so: it bounds what a single
#: keystroke pulls out of the database, and only matters if some name/type pair has more
#: than this many manifest reissues sharing the ranking, which no real item comes close
#: to (the worst are a handful).
_SEARCH_OVERFETCH = 20

_KIND_ITEM_TYPES = {
    "weapon": DESTINY_ITEM_TYPE_WEAPON,
    "armor": DESTINY_ITEM_TYPE_ARMOR,
}


def _plain_name(value: str) -> str:
    """The bare item name from a stored value like ``Chroma Rush (Auto Rifle)``."""
    return value.split(" (")[0].strip()


def _entry(row: t.Any) -> dict[str, t.Any]:
    """One candidate, in the shape the scorers and the JSON response both want."""
    return {
        "name": row.name,
        "hash": int(row.hash),
        "type": row.item_type_display_name or "",
        "item_type": int(row.item_type),
        "icon": row.icon or "",
        "collectible": bool(row.collectible_hash),
        # -1 sorts below any real season, so items without one fall back to the hash
        # tiebreak — exactly the prior behaviour for them.
        "season": -1 if row.season_number is None else int(row.season_number),
    }


_COLUMNS = (
    schemas.ManifestItem.name,
    schemas.ManifestItem.hash,
    schemas.ManifestItem.item_type_display_name,
    schemas.ManifestItem.item_type,
    schemas.ManifestItem.icon,
    schemas.ManifestItem.collectible_hash,
    schemas.ManifestItem.season_number,
)

#: Only weapons and armour are offered; the item table holds everything else too.
_SEARCHABLE = schemas.ManifestItem.item_type.in_(
    [DESTINY_ITEM_TYPE_WEAPON, DESTINY_ITEM_TYPE_ARMOR]
)


async def ready(version_id: int | None = None) -> bool:
    """Whether there is a manifest to read at all.

    Kept (async now) because callers use it to say so — the backfill script reports
    "no manifest" rather than silently writing a document with no links.
    """
    return (version_id or await current_version_id()) is not None


def _score(entry: dict[str, t.Any], type_hint: str) -> tuple:
    entry_type = (entry["type"] or "").lower()
    return (
        entry["item_type"] == DESTINY_ITEM_TYPE_WEAPON,
        bool(entry_type) and type_hint.startswith(entry_type),
        entry["collectible"],
        # seasonNumber is the authoritative recency key (item hashes aren't
        # chronological); hash is only the final fallback when seasons tie / are -1.
        entry["season"],
        entry["hash"],
    )


async def resolve_light_gg_urls(
    values: t.Iterable[str], version_id: int | None = None
) -> dict[str, str]:
    """Best-effort light.gg URLs for weapon values (``Name (Type)``), keyed by value.

    Every value is answered from **one** query. The single-value entry point below is
    written in terms of this one because the caller that matters — baking a whole
    rotation document's links on save — has a dozen values and used to walk an
    in-memory dict for each; a query per value would have turned a free operation into
    a dozen round-trips.

    A value with no match is simply absent from the result.
    """
    wanted = list(dict.fromkeys(values))
    if not wanted:
        return {}
    version_id = version_id or await current_version_id()
    if version_id is None:
        return {}

    names = {_plain_name(value).lower() for value in wanted}
    async with schemas.db_session() as session:
        rows = (
            await session.execute(
                select(*_COLUMNS).where(
                    schemas.ManifestItem.version_id == version_id,
                    schemas.ManifestItem.name_lower.in_(sorted(names)),
                )
            )
        ).all()

    by_name: dict[str, list[dict[str, t.Any]]] = {}
    for row in rows:
        by_name.setdefault(row.name.lower(), []).append(_entry(row))

    resolved: dict[str, str] = {}
    for value in wanted:
        name = _plain_name(value)
        entries = by_name.get(name.lower())
        if not entries:
            continue
        type_hint = value[len(name) :].strip(" ()").lower()
        best = max(entries, key=lambda entry: _score(entry, type_hint))
        resolved[value] = LIGHT_GG_URL.format(best["hash"])
    return resolved


async def resolve_light_gg_url(value: str, version_id: int | None = None) -> str | None:
    """Best-effort light.gg URL for a weapon value (``Name (Type)``), or ``None``.

    Prefers a weapon whose type matches the ``(Type)`` hint and that is collectible
    (in-game obtainable), then the newest reissue by season number (falling back to the
    highest hash when season data is unavailable)."""
    return (await resolve_light_gg_urls([value], version_id)).get(value)


async def search(
    query: str,
    kind: str | None = None,
    limit: int = 20,
    version_id: int | None = None,
) -> list[dict[str, t.Any]]:
    """Name-substring search for autocomplete. ``kind`` filters to ``weapon``/``armor``.

    Returns ``{name, type, hash, url, icon}`` dicts, prefix matches and collectibles
    first, deduped by (name, type)."""
    q = query.lower().strip()
    if not q:
        return []
    version_id = version_id or await current_version_id()
    if version_id is None:
        return []

    pattern = like_escape(q)
    conditions = [
        schemas.ManifestItem.version_id == version_id,
        _SEARCHABLE,
        schemas.ManifestItem.name_lower.like(f"%{pattern}%", escape=LIKE_ESCAPE),
    ]
    want = _KIND_ITEM_TYPES.get(kind)
    if want is not None:
        conditions.append(schemas.ManifestItem.item_type == want)

    async with schemas.db_session() as session:
        rows = (
            await session.execute(
                select(*_COLUMNS)
                .where(*conditions)
                # The same three keys the in-memory sort used, as CASE expressions so
                # the "0 sorts first" reading is identical on both backends rather than
                # resting on how each renders a boolean under DESC.
                .order_by(
                    case(
                        (
                            schemas.ManifestItem.name_lower.like(
                                f"{pattern}%", escape=LIKE_ESCAPE
                            ),
                            0,
                        ),
                        else_=1,
                    ),
                    case((schemas.ManifestItem.collectible_hash.is_(None), 1), else_=0),
                    schemas.ManifestItem.name,
                )
                .limit(limit * _SEARCH_OVERFETCH)
            )
        ).all()

    seen: set[tuple[str, str]] = set()
    results: list[dict[str, t.Any]] = []
    for row in rows:
        entry = _entry(row)
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
