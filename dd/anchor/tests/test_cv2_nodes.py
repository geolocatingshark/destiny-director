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

"""Unit tests for the Components V2 node model's pure pieces (no Discord I/O).

The authoring UI is now the web builder (its client mirror of this model is covered by
``web_static/tests/cv2_model.test.js``, run by ``make test-js``); here we
exercise the constructors, field specs, mutators, tree ops, add-flow catalogue, preview
sanitiser and validation.
"""

from dd.anchor import cv2_nodes as cn

# --- classification ---------------------------------------------------------------


def test_kind_classifies_each_type():
    assert cn.kind({"type": cn.CONTAINER}) == "container"
    assert cn.kind({"type": cn.TEXT_DISPLAY}) == "text"
    assert cn.kind({"type": cn.SECTION}) == "section"
    assert cn.kind({"type": cn.MEDIA_GALLERY}) == "media"
    assert cn.kind({"type": cn.SEPARATOR}) == "separator"
    assert cn.kind({"type": cn.THUMBNAIL}) == "thumbnail"
    assert cn.kind({"type": cn.BUTTON}) == "link_button"
    # An action-row-wrapped link button classifies the same as a bare button.
    assert cn.kind({"type": cn.ACTION_ROW}) == "link_button"
    assert cn.kind({"type": cn.FILE}) == "file"


# --- preview sanitisation ---------------------------------------------------------
#
# Nodes are literal dicts here. They used to be built with cv2_nodes' make_* helpers,
# but those existed for the in-Discord builder's add-flow and went with it — the web
# builder constructs nodes client-side (web_static/cv2_model.js). Literals also state
# the shape under test outright instead of hiding it behind a constructor.


def test_sanitize_downgrades_incomplete_nodes():
    nodes = [
        {"type": cn.CONTAINER, "components": []},
        {"type": cn.SECTION, "components": []},
        {"type": cn.TEXT_DISPLAY, "content": ""},
    ]
    out = cn.sanitize_for_preview(nodes)

    # Empty container keeps its type but gains a placeholder child.
    assert out[0]["type"] == cn.CONTAINER
    assert out[0]["components"] and out[0]["components"][0]["type"] == cn.TEXT_DISPLAY
    # A section with no text/accessory is downgraded to a placeholder text display.
    assert out[1]["type"] == cn.TEXT_DISPLAY
    assert out[2]["type"] == cn.TEXT_DISPLAY
    # The original nodes are untouched (deep copy).
    assert nodes[0]["components"] == []


def test_sanitize_keeps_valid_section():
    section = {
        "type": cn.SECTION,
        "components": [{"type": cn.TEXT_DISPLAY, "content": "body"}],
        "accessory": {"type": cn.THUMBNAIL, "media": {"url": "https://i.png"}},
    }
    out = cn.sanitize_for_preview([section])
    assert out[0]["type"] == cn.SECTION
    assert out[0]["accessory"]["media"]["url"] == "https://i.png"


# --- validation -------------------------------------------------------------------


def test_validate_flags_problems():
    assert cn.validate([]) == ["The message is empty — add at least one block."]

    problems = cn.validate([{"type": cn.SECTION, "components": []}])
    assert any("1–3 text" in p for p in problems)
    assert any("accessory" in p for p in problems)


def test_validate_passes_a_complete_message():
    container = {
        "type": cn.CONTAINER,
        "components": [{"type": cn.TEXT_DISPLAY, "content": "hello"}],
    }
    assert cn.validate([container]) == []


# --- multi-button rows ---------------------------------------------------------------
# An action row loaded from a live post can hold up to five buttons. `_button_of`
# returns only the first, so validating/sanitizing through it let a broken second
# button reach Discord — and left it uneditable in the web builder.


def _row(*buttons: dict) -> cn.Node:
    return {"type": cn.ACTION_ROW, "components": list(buttons)}


def _btn(label: str = "", url: str = "") -> dict:
    return {"type": cn.BUTTON, "style": 5, "label": label, "url": url}


def test_validate_catches_an_incomplete_second_button():
    problems = cn.validate([_row(_btn("Ok", "https://e.invalid"), _btn("Broken"))])
    assert any("Button 2" in p for p in problems), problems


def test_validate_passes_a_complete_multi_button_row():
    row = _row(_btn("A", "https://e.invalid/a"), _btn("B", "https://e.invalid/b"))
    assert cn.validate([row]) == []


def test_validate_keeps_the_singular_message_for_a_one_button_row():
    problems = cn.validate([_row(_btn("Ok"))])
    assert problems == ["A link button needs both a label and a URL."]


def test_validate_reports_an_empty_row():
    assert any("no buttons" in p for p in cn.validate([_row()]))


def test_sanitize_degrades_a_row_whose_second_button_is_incomplete():
    # Previewing it as-is would make Discord reject the live-preview edit.
    (node,) = cn.sanitize_for_preview(
        [_row(_btn("Ok", "https://e.invalid"), _btn("Broken"))]
    )
    assert node["type"] == cn.TEXT_DISPLAY
    assert "incomplete link button" in node["content"]


def test_sanitize_keeps_a_complete_multi_button_row():
    row = _row(_btn("A", "https://e.invalid/a"), _btn("B", "https://e.invalid/b"))
    assert cn.sanitize_for_preview([row]) == [row]


# --- Discord limits the validator previously let through -----------------------------
# Each of these was silently valid and only failed when Discord refused the send.


def test_validate_refuses_text_over_the_4000_cap():
    long_text = {"type": cn.TEXT_DISPLAY, "content": "x" * 4001}
    assert any("Too much text" in p for p in cn.validate([long_text]))


def test_validate_counts_text_across_the_whole_tree():
    nested = {
        "type": cn.CONTAINER,
        "components": [
            {"type": cn.TEXT_DISPLAY, "content": "x" * 2500},
            {"type": cn.TEXT_DISPLAY, "content": "y" * 2500},
        ],
    }
    assert cn._total_text_len([nested]) == 5000
    assert any("Too much text" in p for p in cn.validate([nested]))


def test_validate_counts_an_astral_glyph_as_two():
    # Discord counts CV2 text in UTF-16 units; counting characters would under-report.
    assert cn._total_text_len([{"type": cn.TEXT_DISPLAY, "content": "🎃"}]) == 2


def test_validate_allows_exactly_the_cap():
    at_cap = {"type": cn.TEXT_DISPLAY, "content": "x" * 4000}
    assert not any("Too much text" in p for p in cn.validate([at_cap]))


def test_validate_refuses_more_than_five_buttons_in_a_row():
    six = _row(*[_btn(f"b{i}", f"https://e.invalid/{i}") for i in range(6)])
    assert any("Discord allows 5" in p for p in cn.validate([six]))


def test_validate_refuses_a_scheme_less_button_url():
    # The common typo; the renderer drops such a button silently, so nothing else
    # would flag it.
    problems = cn.validate([_row(_btn("Go", "kyber3000.com"))])
    assert any("http://" in p for p in problems), problems


def test_validate_refuses_a_javascript_button_url():
    problems = cn.validate([_row(_btn("Go", "javascript:alert(1)"))])
    assert any("http://" in p for p in problems), problems


def test_validate_refuses_an_over_long_button_label():
    problems = cn.validate([_row(_btn("L" * 81, "https://e.invalid"))])
    assert any("Discord allows 80" in p for p in problems), problems


def test_validate_refuses_more_than_ten_gallery_images():
    many = {
        "type": cn.MEDIA_GALLERY,
        "items": [{"media": {"url": f"https://e.invalid/{i}.png"}} for i in range(11)],
    }
    assert any("Discord allows 10" in p for p in cn.validate([many]))
