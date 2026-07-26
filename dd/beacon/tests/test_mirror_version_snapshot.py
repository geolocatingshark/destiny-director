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

"""Unit tests for the worker's version-snapshot serialization (no DB, no real bot).

``_snapshot_payload`` turns the fully-rewritten HMessage a delivery rides on into the
JSON snapshot the web log re-renders. Pins the CV2 build()->raw-dict path (types
normalized to ints), the classic content/embed path (entity-factory embed dicts with
upload resources dropped), the summary extraction, and the over-cap truncation marker.
"""

import typing as t
from types import SimpleNamespace

import hikari as h

from dd.beacon import mirror_worker as mw
from dd.common.bot import CachedFetchBot
from dd.hmessage import HMessage


def _cv2(text: str) -> HMessage:
    return HMessage(
        components=[
            h.impl.ContainerComponentBuilder(
                components=[h.impl.TextDisplayComponentBuilder(content=text)]
            )
        ]
    )


def _stub_bot(serialize_embed: t.Any = None) -> CachedFetchBot:
    # Only .entity_factory.serialize_embed is touched (classic path); cast the stub to
    # the bot type, matching the repo's test convention (t.cast over inline ignores).
    return t.cast(
        CachedFetchBot,
        SimpleNamespace(entity_factory=SimpleNamespace(serialize_embed=serialize_embed)),
    )


def test_snapshot_cv2_serializes_tree_with_int_types() -> None:
    payload, kind, summary = mw._snapshot_payload(
        _cv2("**Weekly reset**\nsecond line"), bot=_stub_bot()
    )
    assert kind == "cv2"
    assert summary == "**Weekly reset**"  # first line only
    container = payload["components"][0]
    assert container["type"] == 17  # ComponentType coerced to a plain int
    assert container["components"][0] == {
        "type": 10,
        "content": "**Weekly reset**\nsecond line",
    }


def test_snapshot_classic_uses_content_first_line() -> None:
    payload, kind, summary = mw._snapshot_payload(
        HMessage(content="Title line\nmore body"), bot=_stub_bot()
    )
    assert kind == "classic"
    assert summary == "Title line"
    assert payload == {"content": "Title line\nmore body", "embeds": []}


def test_snapshot_classic_embed_summary_and_drops_upload_resources() -> None:
    # serialize_embed returns (payload, resources); we keep the dict, drop the upload.
    bot = _stub_bot(lambda e: ({"title": "Embed Title"}, ["UPLOAD"]))
    payload, kind, summary = mw._snapshot_payload(
        HMessage(content="", embeds=[h.Embed(title="Embed Title")]), bot=bot
    )
    assert kind == "classic"
    assert summary == "Embed Title"  # falls back to first embed title
    assert payload == {"content": "", "embeds": [{"title": "Embed Title"}]}


def test_snapshot_truncates_oversized_payload() -> None:
    payload, kind, summary = mw._snapshot_payload(
        _cv2("x" * (mw._MAX_SNAPSHOT_BYTES + 1000)), bot=_stub_bot()
    )
    assert kind == "cv2"
    # body collapsed to a marker; the row still records the version existed.
    assert payload == {"truncated": True}


def test_first_text_line_walks_nested_tree_and_skips_media() -> None:
    tree = [
        {"type": 14},  # separator — no text
        {"type": 12, "items": [{"media": {"url": "http://x"}}]},  # media — no text
        {
            "type": 17,
            "components": [
                {
                    "type": 9,
                    "components": [{"type": 10, "content": "  Deep title  \nx"}],
                }
            ],
        },
    ]
    assert mw._first_text_line(tree) == "Deep title"


def test_first_text_line_none_when_no_text() -> None:
    assert mw._first_text_line([{"type": 14}, {"type": 12, "items": []}]) is None
