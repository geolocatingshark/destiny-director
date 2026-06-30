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

"""Round-trip a bot Components V2 message through the discord.builders editor.

``discord.builders`` is a visual Components V2 builder. It loads its whole state from
the URL hash, encoded as ``"1$" + base64(gzip(JSON.stringify(componentsArray)))`` (see
its ``useHashRouter``), and the same encoding round-trips back out of the address bar.

This module mirrors that encoding in Python so the bot can:

- hand the user a deep-link that opens discord.builders **pre-populated** with an
  existing post's components (:func:`builders_url`), and
- read an edited post back from either a pasted discord.builders URL or raw component
  JSON (:func:`extract_components_from_input`), to apply via ``message.edit``.

The component JSON is fetched raw from Discord (:func:`fetch_raw_message_components`) so
every component type round-trips with full fidelity, independent of what the bot's
in-process model→builder converter happens to support.
"""

import base64
import gzip
import json
import typing as t
import urllib.parse

import aiohttp

from ..common import cfg
from .post_json import RawComponentBuilder, parse_post_json

# Public instance. Point this at a self-hosted fork later (e.g. for an in-app
# "save back to the bot" button) without touching the round-trip encoding.
BUILDERS_BASE_URL = "https://discord.builders"

_DISCORD_API_BASE = "https://discord.com/api/v10"


def encode_builders_hash(components: t.Sequence[t.Mapping[str, t.Any]]) -> str:
    """Encode a components array into a discord.builders URL hash fragment.

    Matches the site's ``encodeState``: gzip the compact JSON, base64 it, prefix
    ``"1$"``. ``mtime=0`` keeps the output deterministic (the gzip header timestamp is
    ignored on decompression anyway).
    """
    raw = json.dumps(components, separators=(",", ":")).encode("utf-8")
    return "1$" + base64.b64encode(gzip.compress(raw, mtime=0)).decode("ascii")


def decode_builders_hash(hash_fragment: str) -> t.Any:
    """Decode a discord.builders URL hash fragment back into its JSON value.

    Handles the current ``"1$"`` gzip format and the site's legacy
    ``base64(encodeURIComponent(json))`` fallback.
    """
    fragment = hash_fragment.strip()
    if fragment.startswith("1$"):
        fragment = fragment[2:]
        padded = fragment + "=" * (-len(fragment) % 4)
        return json.loads(gzip.decompress(base64.b64decode(padded)))
    # Legacy ``decodeStateOld``: base64 → percent-encoded JSON → decode.
    padded = fragment + "=" * (-len(fragment) % 4)
    return json.loads(urllib.parse.unquote(base64.b64decode(padded).decode("utf-8")))


def builders_url(components: t.Sequence[t.Mapping[str, t.Any]]) -> str:
    """Build a discord.builders deep-link pre-loaded with ``components``."""
    return f"{BUILDERS_BASE_URL}/#{encode_builders_hash(components)}"


def extract_components_from_input(raw: str) -> list[RawComponentBuilder]:
    """Parse user input into sendable component builders.

    Accepts a full discord.builders URL, a bare ``"1$"`` hash, or raw component JSON
    (a full message object, bare array, or single component — via
    :func:`parse_post_json`). Raises :class:`ValueError` with a user-facing message on
    anything malformed.
    """
    raw = raw.strip()
    if not raw:
        raise ValueError("No input was provided.")

    # Detect JSON first: a component's markdown ``content`` can itself contain ``#``,
    # so URL detection must not key off a bare ``#``.
    if raw[0] in "[{":
        return parse_post_json(raw)

    hash_fragment: str | None = None
    if raw.startswith("1$"):
        hash_fragment = raw
    elif "#" in raw:
        # A pasted discord.builders URL — everything after the first ``#`` is the state.
        hash_fragment = raw.split("#", 1)[1]

    if hash_fragment is not None:
        try:
            decoded = decode_builders_hash(hash_fragment)
        except Exception as e:
            raise ValueError(
                f"That doesn't look like a valid discord.builders link ({e})."
            ) from e
        return parse_post_json(json.dumps(decoded))

    return parse_post_json(raw)


async def fetch_raw_message_components(
    channel_id: int, message_id: int
) -> list[dict[str, t.Any]]:
    """Fetch a message's raw ``components`` array straight from the Discord REST API.

    Bypasses hikari's model deserialization so every component type round-trips
    verbatim. Authenticates as the anchor bot.
    """
    url = f"{_DISCORD_API_BASE}/channels/{int(channel_id)}/messages/{int(message_id)}"
    headers = {"Authorization": f"Bot {cfg.discord_token_anchor}"}
    async with (
        aiohttp.ClientSession() as session,
        session.get(url, headers=headers) as resp,
    ):
        resp.raise_for_status()
        data = await resp.json()
    return data.get("components") or []
