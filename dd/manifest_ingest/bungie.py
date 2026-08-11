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

"""Talking to Bungie: which manifest is current, and fetching it.

Two calls, deliberately separate, because the cheap one is the hourly fast path:
:func:`current_version` is a small JSON GET and is all a tick does when nothing has
changed, while :func:`fetch_world_content` is the hundreds-of-MB download that runs a
handful of times a month.

Nothing here writes to a durable location. The zip and the extracted sqlite land in a
caller-supplied temporary directory that goes away with the process — there is no cache
to go stale, and therefore no volume to pay for or to pin this service to one host.
"""

from __future__ import annotations

import asyncio
import logging
import zipfile
from pathlib import Path

import aiohttp

logger = logging.getLogger(__name__)

BUNGIE_NET = "https://www.bungie.net"
API_MANIFEST = BUNGIE_NET + "/Platform/Destiny2/Manifest/"

#: Locale. Everything downstream — names, descriptions, type names — assumes English,
#: as the bots always have.
LOCALE = "en"

# The metadata call is tiny; the zip is 43-90 MB compressed and gets a much longer
# allowance. Both are wall-clock totals, not per-read.
_META_TIMEOUT = aiohttp.ClientTimeout(total=30)
_DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=600)


class ManifestUnavailable(Exception):
    """Bungie could not tell us what the current manifest is, or hand it over."""


async def current_version(api_key: str) -> tuple[str, str]:
    """``(version, world-content path)`` for the current manifest.

    ``version`` is Bungie's own version string — the value the ingest compares against
    the stored one to decide whether there is any work to do at all.
    """
    try:
        async with (
            aiohttp.ClientSession(timeout=_META_TIMEOUT) as session,
            session.get(API_MANIFEST, headers={"X-API-Key": api_key}) as response,
        ):
            response.raise_for_status()
            payload = (await response.json())["Response"]
        return payload["version"], payload["mobileWorldContentPaths"][LOCALE]
    except Exception as exc:
        raise ManifestUnavailable(
            f"could not resolve the current manifest: {exc}"
        ) from exc


async def fetch_world_content(path_fragment: str, into: Path) -> Path:
    """Download and extract the world-content sqlite, returning its path.

    The zip is streamed to disk in 1 MiB chunks rather than ``await response.read()``:
    aiohttp builds that by accumulating chunks in a list and then ``b"".join()``-ing
    them, so both copies are alive at the join and the peak is ~2x the payload before
    the first byte is even written. Chunking holds one chunk at a time.
    """
    into.mkdir(parents=True, exist_ok=True)
    archive = into / "manifest.zip"

    try:
        async with (
            aiohttp.ClientSession(timeout=_DOWNLOAD_TIMEOUT) as session,
            session.get(BUNGIE_NET + path_fragment) as response,
        ):
            response.raise_for_status()
            with archive.open("wb") as file:
                async for chunk in response.content.iter_chunked(1 << 20):
                    file.write(chunk)
    except Exception as exc:
        raise ManifestUnavailable(f"could not download the manifest: {exc}") from exc

    def _extract() -> list[str]:
        with zipfile.ZipFile(archive) as zip_ref:
            names = zip_ref.namelist()
            zip_ref.extractall(into)
        return names

    # The extract is CPU-bound and slow enough (~340 MB) to be worth keeping off the
    # loop, even in a process with nothing else to run: it means a download that is
    # still in flight elsewhere is not stalled behind it.
    names = await asyncio.get_running_loop().run_in_executor(None, _extract)
    archive.unlink()

    if not names:
        raise ManifestUnavailable("the manifest archive was empty")
    # Bungie ships exactly one file, named for the version; take what it gave us rather
    # than guessing at a name or globbing the directory.
    extracted = into / names[0]
    if not extracted.is_file():
        raise ManifestUnavailable(f"the manifest archive did not yield a file: {names}")
    logger.info("Extracted the world content to %s", extracted)
    return extracted
