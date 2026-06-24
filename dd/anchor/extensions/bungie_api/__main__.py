"""Standalone smoke test: fetch Xûr and log the weapons/armor on sale.

Run with ``uv run python -OOm dd.anchor.extensions.bungie_api`` (requires a populated
``.env`` and a prior Bungie OAuth login).
"""

import asyncio
import logging

import aiohttp

from dd.common import schemas

from .constants import XUR_VENDOR_HASH
from .manifest import _build_manifest_dict, _get_latest_manifest
from .models import DestinyMembership, DestinyVendor
from .oauth import refresh_api_tokens, webserver_runner_preparation

logger = logging.getLogger(__name__)


async def main():
    runner = webserver_runner_preparation()
    manifest_table = await _build_manifest_dict(
        await _get_latest_manifest(schemas.BungieCredentials.api_key)
    )

    access_token = await refresh_api_tokens(runner)

    async with aiohttp.ClientSession() as session:
        destiny_membership = await DestinyMembership.from_api(session, access_token)
        character_id = await destiny_membership.get_character_id(session, access_token)

    for vendor_hash in [XUR_VENDOR_HASH]:
        vendor = await DestinyVendor.request_from_api(
            destiny_membership=destiny_membership,
            character_id=character_id,
            access_token=access_token,
            manifest_table=manifest_table,
            vendor_hash=vendor_hash,
        )
        logger.info("%s", vendor)
        for item in vendor.sale_items:
            if item.is_armor or item.is_weapon:
                logger.info("%s", item)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
