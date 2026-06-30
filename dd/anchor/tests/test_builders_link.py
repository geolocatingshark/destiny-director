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

"""Unit tests for the discord.builders round-trip encoding (no Discord I/O)."""

import base64
import gzip
import json
import urllib.parse

import pytest

from dd.anchor.builders_link import (
    BUILDERS_BASE_URL,
    builders_url,
    decode_builders_hash,
    encode_builders_hash,
    extract_components_from_input,
)

_COMPONENTS = [
    {
        "type": 17,
        "components": [
            {"type": 10, "content": "# Hello"},
            {"type": 14, "divider": True},
            {"type": 10, "content": "body"},
        ],
    }
]


def test_encode_uses_gzip_v1_format() -> None:
    encoded = encode_builders_hash(_COMPONENTS)
    assert encoded.startswith("1$")
    # The payload is plain gzip of the compact JSON — the format discord.builders'
    # DecompressionStream("gzip") reads.
    decompressed = gzip.decompress(base64.b64decode(encoded[2:]))
    assert json.loads(decompressed) == _COMPONENTS


def test_encode_decode_round_trip() -> None:
    assert decode_builders_hash(encode_builders_hash(_COMPONENTS)) == _COMPONENTS


def test_encode_is_deterministic() -> None:
    # mtime=0 keeps the gzip header (and thus the link) stable for the same input.
    assert encode_builders_hash(_COMPONENTS) == encode_builders_hash(_COMPONENTS)


def test_builders_url_shape() -> None:
    url = builders_url(_COMPONENTS)
    assert url.startswith(f"{BUILDERS_BASE_URL}/#1$")


def test_decode_legacy_format() -> None:
    # The site's pre-gzip fallback: base64(encodeURIComponent(JSON.stringify(state))).
    legacy = base64.b64encode(
        urllib.parse.quote(json.dumps(_COMPONENTS)).encode("utf-8")
    ).decode("ascii")
    assert decode_builders_hash(legacy) == _COMPONENTS


def test_extract_from_full_url() -> None:
    url = builders_url(_COMPONENTS)
    builders = extract_components_from_input(url)
    assert [b.build()[0] for b in builders] == _COMPONENTS


def test_extract_from_bare_hash() -> None:
    builders = extract_components_from_input(encode_builders_hash(_COMPONENTS))
    assert [b.build()[0] for b in builders] == _COMPONENTS


def test_extract_from_raw_json() -> None:
    builders = extract_components_from_input(json.dumps({"components": _COMPONENTS}))
    assert [b.build()[0] for b in builders] == _COMPONENTS


@pytest.mark.parametrize("raw", ["", "   ", "https://discord.builders/#not-base64"])
def test_extract_rejects_bad_input(raw: str) -> None:
    with pytest.raises(ValueError):
        extract_components_from_input(raw)
