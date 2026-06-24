"""Destiny 2 manifest download, caching, and in-memory table building."""

import asyncio
import json
import os
import zipfile
from pathlib import Path

import aiofiles
import aiohttp
import aiosqlite

from .constants import API_MANIFEST, BUNGIE_NET, manifest_table_names

# Timeouts for the manifest fetch. The metadata call is tiny; the manifest zip
# is large (hundreds of MB) so it gets a much longer allowance.
_MANIFEST_META_TIMEOUT = aiohttp.ClientTimeout(total=30)
_MANIFEST_DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=600)


async def _get_latest_manifest(api_key: str) -> str:
    # Prep the manifest directory
    Path("manifest").mkdir(exist_ok=True)

    # Get the latest manifest url from the API
    async with (
        aiohttp.ClientSession(timeout=_MANIFEST_META_TIMEOUT) as session,
        session.get(API_MANIFEST, headers={"X-API-Key": api_key}) as response,
    ):
        manifest_url_fragment = (await response.json())["Response"][
            "mobileWorldContentPaths"
        ]["en"]

    manifest_url_filename = manifest_url_fragment.split("/")[-1]
    # Check if the manifest is already downloaded
    if os.path.exists("manifest/" + manifest_url_filename):
        return "manifest/" + manifest_url_filename

    manifest_url = BUNGIE_NET + manifest_url_fragment

    async with (
        aiohttp.ClientSession(timeout=_MANIFEST_DOWNLOAD_TIMEOUT) as session,
        session.get(manifest_url) as response,
    ):
        manifest_zip = await response.read()

    async with aiofiles.open("manifest.zip", "wb") as file:
        await file.write(manifest_zip)

    # Cleanup manifest directory
    for file in os.listdir("manifest"):
        os.remove("manifest/" + file)

    def _extract():
        # Extract the newly downloaded manifest
        with zipfile.ZipFile("manifest.zip", "r") as zip_ref:
            zip_ref.extractall("manifest")

    await asyncio.get_event_loop().run_in_executor(None, _extract)

    manifest_path = "manifest/" + os.listdir("manifest")[0]
    return manifest_path


async def _build_manifest_dict(manifest_path: str):
    # connect to the manifest
    async with aiosqlite.connect(manifest_path) as con:
        # create a cursor object
        cur = await con.cursor()
        all_data = {}
        # for every table name in the dictionary
        for table_name in manifest_table_names:
            # get a list of all the jsons from the table
            await cur.execute("SELECT json from " + table_name)
            # this returns a list of tuples: the first item in each tuple is our json
            items = await cur.fetchall()
            # create a list of jsons
            item_jsons = [json.loads(item[0]) for item in items]
            # create a dictionary with the hashes as keys
            # and the jsons as values
            item_dict = {}
            for item in item_jsons:
                # add that dictionary to our all_data using the name of the table
                # as a key.
                item_dict[item["hash"]] = item
            all_data[table_name] = item_dict
    return all_data
