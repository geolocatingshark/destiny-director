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

The interactive menu/modal flow in ``cv2_builder`` is verified manually on dev; here we
exercise the constructors, field specs, mutators, tree ops, add-flow catalogue, preview
sanitiser and validation.
"""

from dd.anchor import cv2_nodes as cn

# --- classification ---------------------------------------------------------------


def test_kind_classifies_each_type():
    assert cn.kind(cn.make_container()) == "container"
    assert cn.kind(cn.make_text()) == "text"
    assert cn.kind(cn.make_section()) == "section"
    assert cn.kind(cn.make_media_gallery()) == "media"
    assert cn.kind(cn.make_separator()) == "separator"
    assert cn.kind(cn.make_thumbnail()) == "thumbnail"
    assert cn.kind(cn.make_button()) == "link_button"
    # An action-row-wrapped link button classifies the same as a bare button.
    assert cn.kind(cn.make_link_button()) == "link_button"
    assert cn.kind({"type": 13}) == "file"


def test_is_container_like_and_has_modal():
    assert cn.is_container_like(cn.make_container())
    assert cn.is_container_like(cn.make_section())
    assert not cn.is_container_like(cn.make_text())
    # Sections are managed by drilling in, so they have no direct modal.
    assert not cn.has_modal(cn.make_section())
    assert cn.has_modal(cn.make_container())
    assert cn.has_modal(cn.make_text())


# --- field specs + mutators -------------------------------------------------------


def test_text_roundtrip():
    node = cn.make_text("hello")
    assert cn.text_fields(node) == [("Text", "hello", True, True)]
    cn.mutate_text(node, ["world"])
    assert node["content"] == "world"


def test_container_seeds_default_accent_colour():
    # A fresh builder container carries the brand default so it matches every other
    # container-producing path; the modal can still recolour or clear it (below).
    node = cn.make_container()
    assert node["accent_color"] == int(cn.cfg.embed_default_color)
    # container_fields pre-fills that default into the Edit modal (not blank).
    label, value, *_ = cn.container_fields(node)[0]
    assert value == f"#{int(cn.cfg.embed_default_color):06x}"


def test_container_color_and_spoiler():
    node = cn.make_container()
    out = cn.mutate_container(node, ["#ff0000", "yes"])
    assert out is not None
    assert node["accent_color"] == 0xFF0000
    assert node["spoiler"] is True
    # Blank colour clears it; invalid colour leaves the node untouched.
    cn.mutate_container(node, ["", "no"])
    assert "accent_color" not in node
    assert node["spoiler"] is False
    assert cn.mutate_container(node, ["not-a-color", "no"]) is None


def test_separator_fields_and_mutate():
    node = cn.make_separator()
    assert cn.separator_fields(node) == [
        ("Divider line (yes/no)", "yes", False, False),
        ("Spacing (1 = small, 2 = large)", "1", False, False),
    ]
    cn.mutate_separator(node, ["no", "2"])
    assert node["divider"] is False
    assert node["spacing"] == 2


def test_media_gallery_parses_newline_urls_and_caps_at_ten():
    node = cn.make_media_gallery()
    urls = "\n".join(f"https://x/{i}.png" for i in range(12))
    cn.mutate_media(node, [urls])
    assert len(node["items"]) == 10
    assert node["items"][0] == {"media": {"url": "https://x/0.png"}}
    # Blank lines are ignored.
    cn.mutate_media(node, ["  \nhttps://y/a.png\n\n"])
    assert node["items"] == [{"media": {"url": "https://y/a.png"}}]


def test_link_button_mutate_sets_and_clears_emoji():
    node = cn.make_link_button()
    cn.mutate_link_button(node, ["Click", "https://z", "😀"])
    button = node["components"][0]
    assert button["label"] == "Click"
    assert button["url"] == "https://z"
    assert button["emoji"] == {"name": "😀"}
    assert button["style"] == 5
    cn.mutate_link_button(node, ["Click", "https://z", ""])
    assert "emoji" not in node["components"][0]


def test_thumbnail_mutate():
    node = cn.make_thumbnail()
    cn.mutate_thumbnail(node, ["https://i", "alt", "yes"])
    assert node["media"] == {"url": "https://i"}
    assert node["description"] == "alt"
    assert node["spoiler"] is True
    cn.mutate_thumbnail(node, ["https://i", "", "no"])
    assert "description" not in node


# --- add-flow catalogue -----------------------------------------------------------


def test_addable_kinds_scoped_by_nesting_rules():
    nodes = [cn.make_container()]
    # Root: container is offered (top-level only).
    assert "container" in cn.addable_kinds(nodes, [])
    # Inside a container: everything but a nested container.
    assert "container" not in cn.addable_kinds(nodes, [0])
    assert "text" in cn.addable_kinds(nodes, [0])
    # Inside a section: only text + accessory pseudo-kinds.
    section_nodes = [cn.make_section()]
    assert cn.addable_kinds(section_nodes, [0]) == [
        "text",
        "acc_thumbnail",
        "acc_link_button",
    ]


def test_add_constructors_and_modal_flags():
    assert cn.opens_modal_on_add("text") is True
    assert cn.opens_modal_on_add("separator") is False
    assert cn.opens_modal_on_add("container") is False
    assert cn.is_accessory_kind("acc_thumbnail") is True
    assert cn.is_accessory_kind("text") is False
    # A section accessory link button is a bare button, not an action row.
    assert cn.new_node_for("acc_link_button")["type"] == cn.BUTTON
    assert cn.new_node_for("link_button")["type"] == cn.ACTION_ROW


# --- tree navigation + edits ------------------------------------------------------


def _sample_tree() -> list[cn.Node]:
    container = cn.make_container()
    container["components"] = [cn.make_text("a"), cn.make_text("b")]
    return [container, cn.make_separator()]


def test_resolve_path_and_scope_children():
    nodes = _sample_tree()
    assert cn.scope_children(nodes, []) is nodes
    assert cn.resolve_path(nodes, [0])["type"] == cn.CONTAINER
    assert cn.scope_children(nodes, [0])[1]["content"] == "b"
    assert cn.scope_is_section(nodes, [0]) is False


def test_insert_delete_move():
    nodes = _sample_tree()
    cn.insert_node(nodes, [0], 1, cn.make_text("x"))
    assert [c["content"] for c in cn.scope_children(nodes, [0])] == ["a", "x", "b"]
    cn.delete_node(nodes, [0], 0)
    assert [c["content"] for c in cn.scope_children(nodes, [0])] == ["x", "b"]
    new_index = cn.move_node(nodes, [0], 0, 1)
    assert new_index == 1
    assert [c["content"] for c in cn.scope_children(nodes, [0])] == ["b", "x"]
    # A move off the end is a no-op returning the same index.
    assert cn.move_node(nodes, [0], 1, 1) == 1


def test_insert_into_empty_container_mutates_the_real_list():
    # Regression: an empty container/section must expose its *real* child list so the
    # first inserted child actually lands (not into a throwaway ``[] or []`` list).
    nodes = [cn.make_container()]
    assert cn.scope_children(nodes, [0]) is nodes[0]["components"]
    cn.insert_node(nodes, [0], 0, cn.make_text("first"))
    assert [c["content"] for c in nodes[0]["components"]] == ["first"]

    section = [cn.make_section()]
    cn.insert_node(section, [0], 0, cn.make_text("body"))
    assert section[0]["components"][0]["content"] == "body"


def test_set_accessory():
    nodes = [cn.make_section()]
    cn.set_accessory(nodes, [0], cn.make_thumbnail())
    assert nodes[0]["accessory"]["type"] == cn.THUMBNAIL


# --- labels -----------------------------------------------------------------------


def test_node_label_previews():
    assert cn.node_label(cn.make_text("hi there")).startswith("Text: hi there")
    assert "0 items" in cn.node_label(cn.make_container())
    assert "no accessory" in cn.node_label(cn.make_section())


# --- preview sanitisation ---------------------------------------------------------


def test_sanitize_downgrades_incomplete_nodes():
    empty_container = cn.make_container()
    empty_section = cn.make_section()
    empty_text = cn.make_text("")
    nodes = [empty_container, empty_section, empty_text]
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
    section = cn.make_section()
    section["components"] = [cn.make_text("body")]
    thumb = cn.make_thumbnail()
    thumb["media"]["url"] = "https://i.png"
    section["accessory"] = thumb
    out = cn.sanitize_for_preview([section])
    assert out[0]["type"] == cn.SECTION
    assert out[0]["accessory"]["media"]["url"] == "https://i.png"


# --- validation -------------------------------------------------------------------


def test_validate_flags_problems():
    assert cn.validate([]) == ["The message is empty — add at least one block."]

    section = cn.make_section()
    problems = cn.validate([section])
    assert any("1–3 text" in p for p in problems)
    assert any("accessory" in p for p in problems)


def test_validate_passes_a_complete_message():
    container = cn.make_container()
    container["components"] = [cn.make_text("hello")]
    assert cn.validate([container]) == []
