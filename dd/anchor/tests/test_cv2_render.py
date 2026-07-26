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

"""Golden-render + diff tests for the CV2/classic snapshot renderer (pure, no DB/bot).

Pins the safe-HTML discipline (every text leaf escaped, masked-link + media + button
URLs http(s)-validated, custom emoji → CDN img, only the whitelisted tags emitted), the
strict unknown-node → placeholder degrade, the classic embed card, and the word-level
diff with its structural-change note.
"""

from dd.anchor import cv2_render as r


def _cv2(*nodes) -> dict:
    return {"components": list(nodes)}


def _text(content: str) -> dict:
    return {"type": 10, "content": content}


def test_container_text_with_accent_and_markdown() -> None:
    out = r.render_snapshot(
        _cv2(
            {
                "type": 17,
                "accent_color": 0x3498DB,
                "components": [_text("# Reset\n**bold**")],
            }
        ),
        "cv2",
    )
    assert 'class="cv2-container" style="border-left-color:#3498db"' in out
    assert '<span class="md-h1">Reset</span>' in out
    assert "<strong>bold</strong>" in out


def test_escapes_text_and_drops_non_http_link() -> None:
    out = r.render_snapshot(
        _cv2(_text("<script>alert(1)</script> [x](javascript:alert(1))")), "cv2"
    )
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    # The non-http(s) masked link stays inert text — never an anchor with that scheme.
    assert "<a " not in out
    assert 'href="javascript:' not in out


def test_custom_emoji_resolves_to_cdn_img() -> None:
    static = r.render_snapshot(_cv2(_text("hi <:vex:123>")), "cv2")
    assert (
        '<img class="emoji" src="https://cdn.discordapp.com/emojis/123.png"' in static
    )
    animated = r.render_snapshot(_cv2(_text("<a:spin:456>")), "cv2")
    assert "https://cdn.discordapp.com/emojis/456.gif" in animated


def test_section_thumbnail_and_masked_link() -> None:
    out = r.render_snapshot(
        _cv2(
            {
                "type": 9,
                "components": [_text("body [go](https://ex.com)")],
                "accessory": {"type": 11, "media": {"url": "https://cdn.ex/t.png"}},
            }
        ),
        "cv2",
    )
    assert '<a href="https://ex.com">go</a>' in out
    assert '<img class="cv2-thumb" src="https://cdn.ex/t.png"' in out


def test_media_gallery_and_link_button_row() -> None:
    out = r.render_snapshot(
        _cv2(
            {"type": 12, "items": [{"media": {"url": "https://cdn.ex/a.png"}}]},
            {
                "type": 1,
                "components": [
                    {"type": 2, "style": 5, "label": "Guide", "url": "https://g.ex"}
                ],
            },
        ),
        "cv2",
    )
    assert '<img class="cv2-media-item" src="https://cdn.ex/a.png"' in out
    assert '<a class="cv2-button" href="https://g.ex"' in out and ">Guide</a>" in out


def test_urlless_button_dropped_and_unknown_node_degrades() -> None:
    out = r.render_snapshot(
        _cv2(
            {"type": 2, "style": 2, "label": "NoUrl"},  # interactive/url-less → dropped
            {"type": 13},  # file → labeled placeholder
            {"type": 99},  # unknown → labeled placeholder
        ),
        "cv2",
    )
    assert "NoUrl" not in out
    assert "File attachment" in out
    assert "Unsupported component (type 99)" in out


def test_separator_variants() -> None:
    assert '<hr class="cv2-sep">' in r.render_snapshot(
        _cv2({"type": 14, "divider": True}), "cv2"
    )
    assert 'class="cv2-spacer"' in r.render_snapshot(
        _cv2({"type": 14, "divider": False}), "cv2"
    )


def test_truncated_and_empty_degrade() -> None:
    assert "too large" in r.render_snapshot({"truncated": True}, "cv2")
    assert "no renderable components" in r.render_snapshot(_cv2(), "cv2")


def test_classic_render_with_embed() -> None:
    out = r.render_snapshot(
        {
            "content": "Hello **world**",
            "embeds": [{"title": "T", "description": "desc", "color": 0xFF0000}],
        },
        "classic",
    )
    assert "Classic message — text · 1 embed(s)" in out
    assert "<strong>world</strong>" in out
    assert '<div class="embed-title">T</div>' in out
    assert 'style="border-left-color:#ff0000"' in out


# -- diff --------------------------------------------------------------------


def test_word_diff_marks_added_and_removed() -> None:
    old = _cv2(_text("The quick brown fox"))
    new = _cv2(_text("The slow brown fox jumps"))
    out = r.render_diff(new, "cv2", old, "cv2")
    assert "<del>quick</del>" in out
    assert "<ins>slow</ins>" in out
    assert "<ins> jumps</ins>" in out or "<ins>jumps</ins>" in out
    assert "brown fox" in out  # unchanged text preserved


def test_diff_structural_note_on_image_delta() -> None:
    old = _cv2(_text("body"))
    new = _cv2(
        _text("body"),
        {"type": 12, "items": [{"media": {"url": "https://cdn.ex/a.png"}}]},
    )
    out = r.render_diff(new, "cv2", old, "cv2")
    assert "Structural change" in out and "+1 image" in out


def test_diff_no_changes_reports_none() -> None:
    same = _cv2(_text("identical"))
    out = r.render_diff(same, "cv2", same, "cv2")
    assert "No text or structural changes" in out


def test_diff_truncated_side_degrades() -> None:
    out = r.render_diff(_cv2(_text("x")), "cv2", {"truncated": True}, "cv2")
    assert "Cannot diff" in out
