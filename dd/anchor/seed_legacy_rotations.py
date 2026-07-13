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

"""Idempotent seed of the legacy world-activity rotations into the DB JSON store.

Loads one committed document per destination from ``seed_data/legacy/<key>.json`` (each
holds a weekly-reset ``reference_date`` plus the cycle lists — not the spreadsheet's
dated rows), validates it against the schema, and upserts it under ``legacy_<key>``. By
default an existing row is left untouched (the web editor at ``/rotation edit`` is the
ongoing authoring path); pass ``--force`` to overwrite.

Run: ``uv run --env-file .env python -m dd.anchor.seed_legacy_rotations [--force]``.

Two activities ship as empty (TBC) — the Moon's Nightmare Hunts and the Dares loot table
— because the source data is incomplete / structurally ambiguous; author them via the
editor.
"""

import argparse
import asyncio
import json
import pathlib
import typing as t

from ..common import rotation_schema, schemas
from ..common.legacy_activities import (
    is_weapon_value,
    weapon_slot_values,
    weapon_values,
)
from ..sector_accounting.legacy_activities import LegacyRotation
from .extensions.bungie_api import item_index

_SEED_DIR = pathlib.Path(__file__).resolve().parent / "seed_data" / "legacy"


def _bake_links(doc: dict[str, t.Any]) -> int:
    """Resolve the doc's weapon values to light.gg URLs in place; returns the count."""
    doc.pop("item_links", None)
    links = {
        value: url
        for value in weapon_values(doc)
        if (url := item_index.resolve_light_gg_url(value))
    }
    if links:
        doc["item_links"] = links
    return len(links)


def _unlinked_weapons(doc: dict[str, t.Any]) -> list[str]:
    """Weapon-slot values that ended up without a light.gg link, tagged with the likely
    cause: a mistyped ``(Type)`` (dropped before resolution) vs. an unmatched name.

    A mistyped type is the dangerous one — the value never even reaches resolution, so
    it silently loses its link (exactly how ``Auto Rilfe`` slipped through)."""
    links = doc.get("item_links", {})
    report: list[str] = []
    for value in sorted(weapon_slot_values(doc)):
        if value in links:
            continue
        reason = (
            "unmatched name"
            if is_weapon_value(value)
            else "bad (Type) — check spelling"
        )
        report.append(f"{value!r} [{reason}]")
    return report


async def seed(*, force: bool, only: str | None = None, links: bool = False) -> None:
    if links:
        print("warming the manifest item index… (first run downloads the manifest)")
        await item_index.warm(schemas.BungieCredentials.api_key)
        if not item_index.ready():
            print(
                "WARNING: item index not ready (no API key / manifest); links skipped"
            )

    for key in rotation_schema.LEGACY_DESTINATIONS:
        if only is not None and key != only:
            continue
        slug = f"legacy_{key}"
        doc = json.loads((_SEED_DIR / f"{key}.json").read_text(encoding="utf-8"))

        # Fail loudly on bad seed data rather than storing an unusable document.
        rotation_schema.validate(slug, doc)
        LegacyRotation.from_json(doc)

        existing = await schemas.RotationData.get_data(slug)
        if existing is not None and not force:
            print(f"skip  {slug} (already present; use --force to overwrite)")
            continue

        link_count = _bake_links(doc) if links else 0
        await schemas.RotationData.set_data(slug, doc)
        suffix = f", {link_count} links" if links else ""
        print(f"seed  {slug} ({_value_count(doc)} values{suffix})")
        if links and item_index.ready():
            for entry in _unlinked_weapons(doc):
                print(f"  WARN  {slug}: no light.gg link for {entry}")


def _value_count(doc: dict[str, t.Any]) -> int:
    """Total stored values across a doc's activities (element cycles or set gear)."""
    total = 0
    for activity in doc["activities"]:
        if activity.get("kind") == "sets":
            total += len(activity.get("schedule", []))
            for gear_set in activity.get("sets", []):
                total += len(gear_set.get("weapons", []))
                total += len(gear_set.get("armor", []))
        else:
            total += sum(len(element["values"]) for element in activity["elements"])
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite rows that already exist (default: skip them).",
    )
    parser.add_argument(
        "--only",
        metavar="KEY",
        default=None,
        help="Seed only this destination key (e.g. 'dares'); default: all.",
    )
    parser.add_argument(
        "--links",
        action="store_true",
        help="Also resolve weapon light.gg links from the manifest (needs the "
        "Bungie API key; downloads the manifest on first run).",
    )
    args = parser.parse_args()
    asyncio.run(seed(force=args.force, only=args.only, links=args.links))


if __name__ == "__main__":
    main()
