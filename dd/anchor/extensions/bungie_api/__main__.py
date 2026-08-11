"""Standalone smoke test: fetch Xûr and log the weapons/armor on sale.

Run with ``uv run python -OOm dd.anchor.extensions.bungie_api`` (requires a populated
``.env``, a prior Bungie OAuth login, and a manifest already ingested into the database
— see ``dd.manifest_ingest``).
"""

import asyncio
import logging

import aiohttp

from .client import fetch_vendor
from .constants import XUR_VENDOR_HASH
from .manifest_db import ManifestLookup, require_version_id
from .models import DestinyMembership, DestinyVendor
from .oauth import refresh_api_tokens, webserver_runner_preparation

logger = logging.getLogger(__name__)


async def main():
    runner = webserver_runner_preparation()
    manifest_table = ManifestLookup(await require_version_id())

    access_token = await refresh_api_tokens(runner)

    async with aiohttp.ClientSession() as session:
        destiny_membership = await DestinyMembership.from_api(session, access_token)
        character_id = await destiny_membership.get_character_id(session, access_token)

    for vendor_hash in [XUR_VENDOR_HASH]:
        response = await fetch_vendor(
            access_token=access_token,
            membership_type=destiny_membership.membership_type,
            membership_id=destiny_membership.membership_id,
            character_id=character_id,
            vendor_hash=vendor_hash,
        )
        await manifest_table.preload_vendor_response(response)
        vendor = DestinyVendor.from_vendors_api_response(
            response=response, manifest_table=manifest_table
        )
        logger.info("%s", vendor)
        for item in vendor.sale_items:
            if item.is_armor or item.is_weapon:
                logger.info("%s", item)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
