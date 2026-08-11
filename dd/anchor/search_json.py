"""Developer utility for looking up Destiny item ids by name.

Not loaded by the bot. Resolves an item name to its inventory-item hashes from the
manifest projection in the database (see ``dd.manifest_ingest``), for ad-hoc debugging.
"""

import asyncio
from pathlib import Path
from pprint import pprint

from sqlalchemy import select

from dd.anchor.extensions.bungie_api.manifest_db import require_version_id
from dd.common import schemas

FILE_PATH = Path(__file__).parent.parent.parent / "getprofile.json"
ITEM_NAME = "Ferropotent Robes"


async def item_ids_from_name(item_name: str) -> list[int]:
    """Every inventory-item hash whose name matches ``item_name`` (case-insensitively).

    One indexed query on ``name_lower``. It used to be a walk of the whole item table
    with a per-row ``.lower()`` comparison, which meant this debugging aid downloaded
    the manifest.
    """
    version_id = await require_version_id()
    async with schemas.db_session() as session:
        hashes = list(
            (
                await session.execute(
                    select(schemas.ManifestItem.hash).where(
                        schemas.ManifestItem.version_id == version_id,
                        schemas.ManifestItem.name_lower == item_name.lower().strip(),
                    )
                )
            ).scalars()
        )
    for hash_ in hashes:
        print(f"Found Item ID: {hash_}, Name: {item_name}")
    return [int(h) for h in hashes]


async def search_file(path: Path, item_name: str) -> dict[int, str]:
    """Get the line number of an int in a file."""

    item_ids = await item_ids_from_name(item_name)

    with path.open("r", encoding="utf-8") as f:
        data = f.readlines()

    results = {}
    for line_number, line in enumerate(data):
        if any(str(item_id) in line for item_id in item_ids):
            results[line_number] = line

    return results


if __name__ == "__main__":
    pprint(asyncio.run(search_file(FILE_PATH, ITEM_NAME)))
