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
from ..sector_accounting.legacy_activities import LegacyRotation

_SEED_DIR = pathlib.Path(__file__).resolve().parent / "seed_data" / "legacy"


async def seed(*, force: bool, only: str | None = None) -> None:
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

        await schemas.RotationData.set_data(slug, doc)
        print(f"seed  {slug} ({_value_count(doc)} values)")


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
    args = parser.parse_args()
    asyncio.run(seed(force=args.force, only=args.only))


if __name__ == "__main__":
    main()
