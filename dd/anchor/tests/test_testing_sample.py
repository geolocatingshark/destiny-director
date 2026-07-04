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

"""The /testing convert_sample embeds must exercise every embeds_to_container branch."""

import typing as t

from dd.anchor.extensions.testing import _sample_embeds
from dd.common.components import embeds_to_container

# Discord component type ids the converter can emit.
_SECTION = 9
_TEXT = 10
_THUMBNAIL = 11
_MEDIA_GALLERY = 12
_SEPARATOR = 14
_CONTAINER = 17


def _all_types(node: t.Any) -> list[int]:
    """Every ``type`` id anywhere in a built component payload tree."""
    found: list[int] = []
    if isinstance(node, dict):
        if "type" in node:
            found.append(int(node["type"]))
        for value in node.values():
            found.extend(_all_types(value))
    elif isinstance(node, (list, tuple)):
        for item in node:
            found.extend(_all_types(item))
    return found


def test_sample_embeds_exercise_every_converter_branch():
    payload = embeds_to_container(_sample_embeds()).build()
    types = _all_types(payload)

    # Container root, a thumbnail-anchored section, both a section thumbnail and the
    # image/thumbnail-only media galleries, separators (embed dividers + field
    # separators) and text displays must all be present.
    expected_types = (
        _CONTAINER,
        _SECTION,
        _THUMBNAIL,
        _MEDIA_GALLERY,
        _SEPARATOR,
        _TEXT,
    )
    for expected in expected_types:
        assert expected in types, f"missing component type {expected}"

    # Two media galleries: the kitchen-sink image and the thumbnail-only embed.
    assert types.count(_MEDIA_GALLERY) == 2


def test_sample_container_accent_is_first_embed_colour():
    # First embed is blurple (0x5865F2); the second embed's red must be ignored.
    payload = embeds_to_container(_sample_embeds()).build()
    root = payload[0] if isinstance(payload, tuple) else payload
    assert root["accent_color"] == 0x5865F2
