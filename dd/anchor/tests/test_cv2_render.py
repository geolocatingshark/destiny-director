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

"""Tests for the structural diff — the alignment, not the drawing.

This module used to render CV2 snapshots to HTML. It does not any more: there is one
renderer and it is ``web_static/cv2_render.js`` (see
``plans/preview_renderer_unification.md``). What is left here needs ``difflib``, so it
stayed: aligning two captured versions and annotating what moved.

These name the properties directly. The end-to-end check — that drawing these
annotations reproduces, byte for byte, what the old Python diff renderer emitted — is
the shared corpus in ``dd/anchor/preview_fixtures``, asserted from the JS side.
"""

from dd.anchor import cv2_render as r


def _cv2(*nodes) -> dict:
    return {"components": list(nodes)}


def _text(content: str) -> dict:
    return {"type": 10, "content": content}


def _lines_of(payload: dict) -> list:
    """The first annotated text leaf's line list."""
    return payload["components"][0]["_lines"]


def _diff(new: dict, old: dict) -> dict:
    return r.diff_payload(new, "cv2", old, "cv2")


# --- whole nodes ---------------------------------------------------------------------


def test_an_added_node_is_marked_added() -> None:
    out = _diff(_cv2(_text("a"), _text("b")), _cv2(_text("a")))
    assert out["mode"] == "diff"
    assert [n.get("_mark") for n in out["components"]] == [None, "added"]


def test_a_removed_node_is_marked_removed_and_kept() -> None:
    # The removed node stays in the stream — a diff shows what left, not just what is
    # left over.
    out = _diff(_cv2(_text("a")), _cv2(_text("a"), _text("b")))
    assert [n.get("_mark") for n in out["components"]] == [None, "removed"]
    assert out["components"][1]["content"] == "b"


def test_a_changed_leaf_shows_the_old_going_and_the_new_arriving() -> None:
    old = {"type": 12, "items": [{"media": {"url": "https://ex.com/old.png"}}]}
    new = {"type": 12, "items": [{"media": {"url": "https://ex.com/new.png"}}]}
    out = _diff(_cv2(new), _cv2(old))
    assert [n["_mark"] for n in out["components"]] == ["removed", "added"]


def test_a_container_recurses_rather_than_being_replaced_whole() -> None:
    # The point of a structural diff: one edited line inside a container marks that
    # line, not the entire card.
    old = {"type": 17, "components": [_text("keep"), _text("before")]}
    new = {"type": 17, "components": [_text("keep"), _text("after")]}
    out = _diff(_cv2(new), _cv2(old))

    container = out["components"][0]
    assert container.get("_mark") is None
    assert container["components"][0].get("_lines") is None  # untouched line
    assert container["components"][1]["_lines"]  # the edited one


# --- text leaves ---------------------------------------------------------------------


def test_an_unchanged_line_keeps_its_markdown() -> None:
    out = _diff(_cv2(_text("## Head\nchanged")), _cv2(_text("## Head\noriginal")))
    lines = _lines_of(out)
    assert lines[0] == {"op": "equal", "line": "## Head"}


def test_an_inserted_line_is_marked_ins_and_keeps_its_text() -> None:
    out = _diff(_cv2(_text("one\n- two")), _cv2(_text("one")))
    assert _lines_of(out)[-1] == {"op": "ins", "line": "- two"}


def test_a_deleted_line_is_marked_del() -> None:
    out = _diff(_cv2(_text("one")), _cv2(_text("one\n- two")))
    assert {"op": "del", "line": "- two"} in _lines_of(out)


def test_a_replaced_block_is_word_diffed_over_raw_text() -> None:
    out = _diff(
        _cv2(_text("Nightfall is The Corrupted")),
        _cv2(_text("Nightfall is The Arms Dealer")),
    )
    runs = _lines_of(out)[0]["runs"]
    assert runs[0] == {"op": "equal", "text": "Nightfall is The "}
    assert {"op": "del", "text": "Arms Dealer"} in runs
    assert {"op": "ins", "text": "Corrupted"} in runs


def test_word_runs_preserve_whitespace_exactly() -> None:
    # Whitespace runs are their own tokens, so re-joining the run texts reproduces the
    # original spacing — otherwise a diff would silently reflow the message.
    runs = r._word_runs("a  b\nc", "a  b\nd")
    assert "".join(run["text"] for run in runs if run["op"] != "del") == "a  b\nd"


def test_identical_text_carries_no_line_annotation() -> None:
    out = _diff(_cv2(_text("same"), _text("x")), _cv2(_text("same"), _text("y")))
    assert out["components"][0].get("_lines") is None


# --- section accessories -------------------------------------------------------------


def _section(accessory=None) -> dict:
    node = {"type": 9, "components": [_text("body")]}
    if accessory is not None:
        node["accessory"] = accessory
    return node


_THUMB_A = {"type": 11, "media": {"url": "https://ex.com/a.png"}}
_THUMB_B = {"type": 11, "media": {"url": "https://ex.com/b.png"}}


def test_accessory_gained() -> None:
    out = _diff(_cv2(_section(_THUMB_A)), _cv2(_section()))
    assert [a["_mark"] for a in out["components"][0]["accessory"]] == ["added"]


def test_accessory_lost() -> None:
    out = _diff(_cv2(_section()), _cv2(_section(_THUMB_A)))
    assert [a["_mark"] for a in out["components"][0]["accessory"]] == ["removed"]


def test_accessory_swapped_shows_both() -> None:
    # The three-state case: neither "added" nor "removed" alone tells the story.
    out = _diff(_cv2(_section(_THUMB_B)), _cv2(_section(_THUMB_A)))
    assert [a["_mark"] for a in out["components"][0]["accessory"]] == [
        "removed",
        "added",
    ]


def test_an_unchanged_accessory_stays_a_plain_node() -> None:
    out = _diff(
        _cv2(_section(_THUMB_A), _text("x")), _cv2(_section(_THUMB_A), _text("y"))
    )
    assert out["components"][0]["accessory"] == _THUMB_A


# --- the three ways a diff ends ------------------------------------------------------


def test_no_changes_is_reported_rather_than_shown_blank() -> None:
    out = _diff(_cv2(_text("same")), _cv2(_text("same")))
    assert out["note"] == "No changes from the previous version."


def test_a_changed_message_carries_no_note() -> None:
    assert _diff(_cv2(_text("a")), _cv2(_text("b")))["note"] is None


def test_a_format_change_falls_back_to_the_current_version() -> None:
    out = r.diff_payload(
        _cv2(_text("now cv2")), "cv2", {"content": "was classic"}, "classic"
    )
    assert out["mode"] == "snapshot"
    assert "format changed" in out["note"]


def test_a_truncated_side_cannot_be_diffed() -> None:
    out = r.diff_payload(_cv2(_text("fine")), "cv2", {"truncated": True}, "cv2")
    assert out["mode"] == "placeholder"
    assert "truncated" in out["message"]


# --- classic -------------------------------------------------------------------------


def test_classic_content_is_line_diffed_and_embeds_marked() -> None:
    out = r.diff_payload(
        {"content": "hello there", "embeds": [{"title": "A"}, {"title": "B"}]},
        "classic",
        {"content": "hello world", "embeds": [{"title": "A"}]},
        "classic",
    )
    assert out["kind"] == "classic"
    assert out["content"]["_lines"]
    assert [e.get("_mark") for e in out["embeds"]] == [None, "added"]


def test_classic_with_no_text_either_side_has_no_content_block() -> None:
    out = r.diff_payload(
        {"content": "", "embeds": [{"title": "A"}]},
        "classic",
        {"content": "", "embeds": []},
        "classic",
    )
    assert out["content"] is None
