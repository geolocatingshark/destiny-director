"""Developer utility for looking up Destiny item ids by name.

Not loaded by the bot. Resolves an item name to its inventory-item hashes using
the downloaded Bungie manifest, for ad-hoc debugging.
"""

import asyncio
import typing as t
from pathlib import Path
from pprint import pprint

from dd.anchor.extensions import bungie_api as b
from dd.common import schemas

FILE_PATH = Path(__file__).parent.parent.parent / "getprofile.json"
ITEM_NAME = "Ferropotent Robes"


async def item_ids_from_name(item_name: str) -> list[str]:
    manifest = await b._build_manifest_dict(
        await b._get_latest_manifest(schemas.BungieCredentials.api_key)
    )
    inventory_items_dict: dict[str, t.Any] = manifest["DestinyInventoryItemDefinition"]

    item_ids: list[str] = []
    for item_id, item_data in inventory_items_dict.items():
        item_name_in_data: str = item_data.get("displayProperties", {}).get("name", "")
        if item_name.lower().strip() == item_name_in_data.lower().strip():
            print(f"Found Item ID: {item_id}, Name: {item_name_in_data}")
            item_ids.append(item_id)

    return item_ids


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
    loop = asyncio.get_event_loop()
    results = loop.run_until_complete(search_file(FILE_PATH, ITEM_NAME))
    pprint(results)
