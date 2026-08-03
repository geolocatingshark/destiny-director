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

"""Tests for :func:`cv2_html.render_cv2_nodes_html`.

The walker itself is covered by ``test_cv2_render.py``; what matters here is the three
things the *wiring* is responsible for — sanitizing before rendering, substituting
author-typed ``:name:`` shortcodes, and never emitting an unescaped leaf or a
non-``http(s)`` URL.
"""

import types
import typing as t

import hikari as h

from dd.anchor.cv2_html import render_cv2_nodes_html
from dd.anchor.cv2_nodes import (
    ACTION_ROW,
    BUTTON,
    CONTAINER,
    MEDIA_GALLERY,
    SECTION,
    TEXT_DISPLAY,
)


def _emoji(url: str) -> h.Emoji:
    """A stand-in for ``hikari.Emoji``.

    ``_html_emoji_substituter`` reads the object by duck-typing (``getattr(emoji,
    "url", "")``), so a namespace is enough; the cast keeps the call sites typed without
    constructing a real hikari emoji.
    """
    return t.cast(h.Emoji, types.SimpleNamespace(url=url))


EMOJI: dict[str, h.Emoji] = {
    "kyber": _emoji("https://cdn.discordapp.com/emojis/1.png")
}


def test_renders_a_container_with_accent_and_text():
    html_out = render_cv2_nodes_html(
        [
            {
                "type": CONTAINER,
                "accent_color": 0xEC42A5,
                "components": [{"type": TEXT_DISPLAY, "content": "# Weekly Reset"}],
            }
        ],
        {},
    )
    assert 'class="cv2-container"' in html_out
    assert "border-left-color:#ec42a5" in html_out
    assert 'class="md-h1"' in html_out
    assert "Weekly Reset" in html_out


def test_mid_construction_tree_degrades_instead_of_erroring():
    """An empty container and an accessory-less section are the states the builder is
    in for most of a session; they must preview, not raise.

    ``sanitize_for_preview`` degrades these to a ``-# ⚠️ …`` text node (rendered as
    ``md-small``), *not* to ``cv2_render``'s own ``.cv2-placeholder`` box — that one is
    reserved for nodes the walker cannot render at all (unknown type, truncated
    snapshot). Asserting the real shape keeps this honest about which layer degraded.
    """
    html_out = render_cv2_nodes_html(
        [
            {"type": CONTAINER, "components": []},
            {"type": SECTION, "components": [], "accessory": None},
        ],
        {},
    )
    assert 'class="md-small"' in html_out
    assert "empty container" in html_out
    assert "section — add 1–3 text blocks and an accessory" in html_out


def test_substitutes_author_typed_shortcodes():
    html_out = render_cv2_nodes_html(
        [{"type": TEXT_DISPLAY, "content": "Loot :kyber: incoming"}], EMOJI
    )
    assert '<img class="emoji"' in html_out
    assert "cdn.discordapp.com/emojis/1.png" in html_out


def test_unknown_shortcode_stays_escaped_text():
    html_out = render_cv2_nodes_html(
        [{"type": TEXT_DISPLAY, "content": "Loot :nosuchemoji: incoming"}], EMOJI
    )
    assert "<img" not in html_out
    assert ":nosuchemoji:" in html_out


def test_text_leaves_are_escaped():
    html_out = render_cv2_nodes_html(
        [{"type": TEXT_DISPLAY, "content": "<script>alert(1)</script>"}], {}
    )
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_non_http_urls_are_dropped():
    """A javascript: button URL must not survive into an href."""
    html_out = render_cv2_nodes_html(
        [
            {
                "type": ACTION_ROW,
                "components": [
                    {
                        "type": BUTTON,
                        "style": 5,
                        "label": "Click",
                        "url": "javascript:alert(1)",
                    }
                ],
            }
        ],
        {},
    )
    assert "javascript:" not in html_out


def test_media_gallery_keeps_only_http_items():
    html_out = render_cv2_nodes_html(
        [
            {
                "type": MEDIA_GALLERY,
                "items": [
                    {"media": {"url": "https://example.invalid/a.png"}},
                    {"media": {"url": "file:///etc/passwd"}},
                ],
            }
        ],
        {},
    )
    assert "example.invalid/a.png" in html_out
    assert "file:///" not in html_out


def test_empty_node_list_renders_a_placeholder_not_an_exception():
    assert "cv2-placeholder" in render_cv2_nodes_html([], {})


# --- media alt text + spoiler ---------------------------------------------------------
# Discord supports both per gallery item; the render dropped them, so an image-only post
# reached a screen reader with nothing to describe it and a spoiler previewed unblurred.


def test_gallery_item_description_becomes_alt_text():
    html_out = render_cv2_nodes_html(
        [
            {
                "type": MEDIA_GALLERY,
                "items": [
                    {
                        "media": {"url": "https://example.invalid/a.png"},
                        "description": "The Corrupted, Master",
                    }
                ],
            }
        ],
        {},
    )
    assert 'alt="The Corrupted, Master"' in html_out


def test_gallery_item_alt_text_is_escaped():
    html_out = render_cv2_nodes_html(
        [
            {
                "type": MEDIA_GALLERY,
                "items": [
                    {
                        "media": {"url": "https://example.invalid/a.png"},
                        "description": '"><script>alert(1)</script>',
                    }
                ],
            }
        ],
        {},
    )
    assert "<script>" not in html_out
    assert "&quot;" in html_out


def test_a_spoilered_gallery_item_is_marked():
    html_out = render_cv2_nodes_html(
        [
            {
                "type": MEDIA_GALLERY,
                "items": [
                    {"media": {"url": "https://example.invalid/a.png"}, "spoiler": True}
                ],
            }
        ],
        {},
    )
    assert "cv2-spoiler" in html_out


def test_a_plain_gallery_item_is_not_marked_spoiler():
    html_out = render_cv2_nodes_html(
        [
            {
                "type": MEDIA_GALLERY,
                "items": [{"media": {"url": "https://example.invalid/a.png"}}],
            }
        ],
        {},
    )
    assert "cv2-spoiler" not in html_out
