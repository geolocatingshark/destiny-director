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

"""Unit tests for the ``/post json`` parser (no Discord I/O)."""

import json

import hikari as h
import pytest

from dd.anchor.post_json import parse_post_json


def test_parses_full_message_object() -> None:
    raw = json.dumps(
        {
            "flags": 32768,
            "components": [{"type": 17, "components": [{"type": 10, "content": "hi"}]}],
        }
    )
    builders = parse_post_json(raw)
    assert len(builders) == 1
    payload, attachments = builders[0].build()
    assert payload == {"type": 17, "components": [{"type": 10, "content": "hi"}]}
    assert list(attachments) == []


def test_parses_bare_array() -> None:
    builders = parse_post_json('[{"type": 17, "components": []}]')
    assert [b.type for b in builders] == [17]


def test_parses_single_component_object() -> None:
    builders = parse_post_json('{"type": 10, "content": "hello"}')
    assert len(builders) == 1
    assert builders[0].type == 10


def test_type_passthrough_matches_v2_enum() -> None:
    # The plain int must compare equal to hikari's enum so create_message auto-sets
    # the IS_COMPONENTS_V2 flag for the message.
    builder = parse_post_json('[{"type": 17}]')[0]
    assert builder.type == h.ComponentType.CONTAINER


def test_preserves_component_id() -> None:
    builder = parse_post_json('[{"type": 17, "id": 5}]')[0]
    assert builder.id == 5


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "not json",
        "{}",
        '{"components": []}',
        '{"components": [{"content": "no type"}]}',
        "42",
    ],
)
def test_rejects_bad_input(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_post_json(raw)
