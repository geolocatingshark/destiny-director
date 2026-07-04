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

"""Pure Components V2 node model for the in-Discord builder (no Discord I/O).

A "node" is a raw Discord component-payload dict (the same JSON shape the REST API sends
and accepts). The builder holds an ordered ``list[Node]`` of top-level nodes and mutates
it via the pure helpers here; the interactive/menu layer lives in ``cv2_builder``.

Nesting mirrors Discord's real rules, not an idealised tree:

- **Container** (``17``) is *top-level only* — it cannot be nested inside another
  container — and holds the other display components plus link-button action rows.
- **Section** (``9``) holds 1–3 Text Displays plus exactly one *accessory* (a Thumbnail
  or a link button).
- **Thumbnail** (``11``) is valid *only* as a section accessory, never a free-standing
  block.
- **File** (``13``) round-trips when editing an existing post but can't be authored here
  (it needs a real uploaded attachment, not a URL).

So the drill-down scope is at most three deep: root → container → section.

Each editable kind exposes a ``*_fields`` function returning ``_FieldSpec`` tuples
(``(label, value, required, multi_line)``, matching ``embeds.py``) and a ``mutate_*``
function applying a modal's values back onto the node. Text-content emoji substitution
is async and lives in the builder, so ``mutate_text`` stores the content verbatim.
"""

import typing as t

import hikari as h

# --- Discord component type ids ---------------------------------------------------

ACTION_ROW = 1
BUTTON = 2
SECTION = 9
TEXT_DISPLAY = 10
THUMBNAIL = 11
MEDIA_GALLERY = 12
FILE = 13
SEPARATOR = 14
CONTAINER = 17

_LINK_BUTTON_STYLE = 5  # hikari.ButtonStyle.LINK
_MAX_GALLERY_ITEMS = 10
_MAX_SECTION_TEXTS = 3
_MAX_TOP_LEVEL = 10

Node = dict[str, t.Any]
# (label, current value, required, multi-line) for one modal text input.
_FieldSpec = tuple[str, str, bool, bool]


# --- classification ---------------------------------------------------------------


def kind(node: Node) -> str:
    """Classify a node into a builder "kind" (``text``, ``container``, …).

    An action row is only ever a link-button row here, so it classifies as
    ``link_button`` (the same kind as a bare link button).
    """
    ty = node.get("type")
    return {
        CONTAINER: "container",
        TEXT_DISPLAY: "text",
        SECTION: "section",
        MEDIA_GALLERY: "media",
        SEPARATOR: "separator",
        FILE: "file",
        THUMBNAIL: "thumbnail",
        ACTION_ROW: "link_button",
        BUTTON: "link_button",
    }.get(ty, "unknown")  # type: ignore[arg-type]


def is_container_like(node: Node) -> bool:
    """Whether a node holds drill-into children (a container or a section)."""
    return node.get("type") in (CONTAINER, SECTION)


def has_modal(node: Node) -> bool:
    """Whether editing this node opens a modal (vs. being managed by drilling in)."""
    return kind(node) in _FIELDS


# --- constructors -----------------------------------------------------------------


def make_container() -> Node:
    return {"type": CONTAINER, "components": []}


def make_text(content: str = "") -> Node:
    return {"type": TEXT_DISPLAY, "content": content}


def make_section() -> Node:
    return {"type": SECTION, "components": []}


def make_media_gallery() -> Node:
    return {"type": MEDIA_GALLERY, "items": []}


def make_separator() -> Node:
    return {"type": SEPARATOR, "divider": True, "spacing": 1}


def make_thumbnail() -> Node:
    return {"type": THUMBNAIL, "media": {"url": ""}}


def make_button() -> Node:
    """A bare link button, as used for a section accessory."""
    return {"type": BUTTON, "style": _LINK_BUTTON_STYLE}


def make_link_button() -> Node:
    """A link button wrapped in its own action row (buttons can't be loose children)."""
    return {"type": ACTION_ROW, "components": [make_button()]}


# --- field specs + mutators (pure) ------------------------------------------------


def _parse_bool(value: str, *, default: bool = False) -> bool:
    v = value.strip().lower()
    if v in ("y", "yes", "true", "1", "on"):
        return True
    if v in ("n", "no", "false", "0", "off"):
        return False
    return default


def text_fields(node: Node) -> list[_FieldSpec]:
    return [("Text", node.get("content", ""), True, True)]


def mutate_text(node: Node, values: list[str]) -> Node | None:
    # Content is stored verbatim; the builder substitutes user-side emoji first.
    node["content"] = values[0]
    return node


def container_fields(node: Node) -> list[_FieldSpec]:
    color = node.get("accent_color")
    color_str = "" if color is None else f"#{int(color):06x}"
    spoiler = "yes" if node.get("spoiler") else "no"
    return [
        ("Accent colour (hex, blank = none)", color_str, False, False),
        ("Spoiler (yes/no)", spoiler, False, False),
    ]


def mutate_container(node: Node, values: list[str]) -> Node | None:
    raw = values[0].strip()
    if not raw:
        node.pop("accent_color", None)
    else:
        try:
            node["accent_color"] = int(h.Color.of(raw))
        except ValueError:
            return None  # invalid colour → leave the node untouched
    node["spoiler"] = _parse_bool(values[1])
    return node


def separator_fields(node: Node) -> list[_FieldSpec]:
    divider = "yes" if node.get("divider", True) else "no"
    spacing = "2" if node.get("spacing", 1) == 2 else "1"
    return [
        ("Divider line (yes/no)", divider, False, False),
        ("Spacing (1 = small, 2 = large)", spacing, False, False),
    ]


def mutate_separator(node: Node, values: list[str]) -> Node | None:
    node["divider"] = _parse_bool(values[0], default=True)
    node["spacing"] = 2 if values[1].strip() == "2" else 1
    return node


def media_fields(node: Node) -> list[_FieldSpec]:
    urls = "\n".join(
        item.get("media", {}).get("url", "") for item in node.get("items", [])
    )
    return [("Image URLs (one per line, max 10)", urls, True, True)]


def mutate_media(node: Node, values: list[str]) -> Node | None:
    urls = [u.strip() for u in values[0].splitlines() if u.strip()][:_MAX_GALLERY_ITEMS]
    node["items"] = [{"media": {"url": u}} for u in urls]
    return node


def _button_of(node: Node) -> Node:
    """The button dict inside a link-button node (unwrapping the action row)."""
    if node.get("type") == ACTION_ROW:
        return node["components"][0]
    return node


def link_button_fields(node: Node) -> list[_FieldSpec]:
    b = _button_of(node)
    emoji = b.get("emoji") or {}
    emoji_str = emoji.get("name", "") if isinstance(emoji, dict) else ""
    return [
        ("Label", b.get("label", ""), True, False),
        ("URL", b.get("url", ""), True, False),
        ("Emoji (optional, one character)", emoji_str, False, False),
    ]


def mutate_link_button(node: Node, values: list[str]) -> Node | None:
    b = _button_of(node)
    b["style"] = _LINK_BUTTON_STYLE
    b["label"] = values[0]
    b["url"] = values[1]
    emoji = values[2].strip()
    if emoji:
        b["emoji"] = {"name": emoji}
    else:
        b.pop("emoji", None)
    return node


def thumbnail_fields(node: Node) -> list[_FieldSpec]:
    return [
        ("Image URL", node.get("media", {}).get("url", ""), True, False),
        ("Description (optional)", node.get("description", ""), False, False),
        ("Spoiler (yes/no)", "yes" if node.get("spoiler") else "no", False, False),
    ]


def mutate_thumbnail(node: Node, values: list[str]) -> Node | None:
    node["media"] = {"url": values[0]}
    if values[1].strip():
        node["description"] = values[1]
    else:
        node.pop("description", None)
    node["spoiler"] = _parse_bool(values[2])
    return node


_FieldsFn = t.Callable[[Node], list[_FieldSpec]]
_MutateFn = t.Callable[[Node, list[str]], Node | None]

# Kinds with a modal (leaf edits + the container's own props). Sections are managed by
# drilling in, not a modal, so they are absent.
_FIELDS: dict[str, _FieldsFn] = {
    "text": text_fields,
    "container": container_fields,
    "separator": separator_fields,
    "media": media_fields,
    "link_button": link_button_fields,
    "thumbnail": thumbnail_fields,
}
_MUTATORS: dict[str, _MutateFn] = {
    "text": mutate_text,
    "container": mutate_container,
    "separator": mutate_separator,
    "media": mutate_media,
    "link_button": mutate_link_button,
    "thumbnail": mutate_thumbnail,
}


def fields_for(node: Node) -> list[_FieldSpec]:
    return _FIELDS[kind(node)](node)


def mutator_for(node: Node) -> _MutateFn:
    return _MUTATORS[kind(node)]


# --- add-flow catalogue -----------------------------------------------------------

# Add-type label per kind (pseudo-kinds ``acc_*`` set a section's accessory).
ADD_LABELS: dict[str, str] = {
    "container": "Container",
    "text": "Text",
    "section": "Section",
    "media": "Media gallery",
    "separator": "Separator",
    "link_button": "Link button",
    "acc_thumbnail": "Accessory: thumbnail image",
    "acc_link_button": "Accessory: link button",
}

# Kinds inserted immediately (no modal); the rest open their edit modal on add.
_IMMEDIATE_ADD = ("container", "section", "separator")

_ADD_CONSTRUCTORS: dict[str, t.Callable[[], Node]] = {
    "container": make_container,
    "text": make_text,
    "section": make_section,
    "media": make_media_gallery,
    "separator": make_separator,
    "link_button": make_link_button,
    "acc_thumbnail": make_thumbnail,
    # A section accessory link button is a *bare* button, not an action row.
    "acc_link_button": make_button,
}


def new_node_for(add_kind: str) -> Node:
    return _ADD_CONSTRUCTORS[add_kind]()


def opens_modal_on_add(add_kind: str) -> bool:
    return add_kind not in _IMMEDIATE_ADD


def is_accessory_kind(add_kind: str) -> bool:
    return add_kind.startswith("acc_")


def addable_kinds(nodes: list[Node], scope_path: list[int]) -> list[str]:
    """The add-types valid in the given scope, per Discord's nesting rules."""
    if scope_is_section(nodes, scope_path):
        # A section holds text displays plus one accessory (thumbnail or link button).
        return ["text", "acc_thumbnail", "acc_link_button"]
    base = ["text", "section", "media", "separator", "link_button"]
    if not scope_path:
        base.insert(0, "container")  # containers are top-level only
    return base


# --- tree navigation + edits (pure) -----------------------------------------------


def children_ref(node: Node) -> list[Node] | None:
    """The mutable child list of a container/section, or ``None`` for a leaf."""
    if node.get("type") in (CONTAINER, SECTION):
        return node.setdefault("components", [])
    return None


def resolve_path(nodes: list[Node], path: list[int]) -> Node:
    """The node at ``path`` (a sequence of child indices from the root list)."""
    node: Node = {"type": CONTAINER, "components": nodes}
    for idx in path:
        children = children_ref(node)
        assert children is not None, "path descends into a leaf node"
        node = children[idx]
    return node


def scope_children(nodes: list[Node], scope_path: list[int]) -> list[Node]:
    """The children of the container/section at ``scope_path`` (root list if empty).

    Returns the node's *actual* child-list reference (so inserts/moves mutate it), even
    when it is empty — hence the explicit ``is None`` check rather than ``or []``, which
    would swap an empty-but-real list for a throwaway one.
    """
    if not scope_path:
        return nodes
    children = children_ref(resolve_path(nodes, scope_path))
    return children if children is not None else []


def scope_is_section(nodes: list[Node], scope_path: list[int]) -> bool:
    return bool(scope_path) and resolve_path(nodes, scope_path).get("type") == SECTION


def insert_node(
    nodes: list[Node], scope_path: list[int], index: int, node: Node
) -> None:
    scope_children(nodes, scope_path).insert(index, node)


def delete_node(nodes: list[Node], scope_path: list[int], index: int) -> None:
    del scope_children(nodes, scope_path)[index]


def move_node(nodes: list[Node], scope_path: list[int], index: int, delta: int) -> int:
    """Swap the child at ``index`` with its neighbour; return its new index."""
    children = scope_children(nodes, scope_path)
    target = index + delta
    if 0 <= target < len(children):
        children[index], children[target] = children[target], children[index]
        return target
    return index


def set_accessory(nodes: list[Node], section_path: list[int], accessory: Node) -> None:
    resolve_path(nodes, section_path)["accessory"] = accessory


# --- labels + previews ------------------------------------------------------------


def _preview(text: str, length: int = 40) -> str:
    one_line = " ".join(text.split())
    return one_line[: length - 1] + "…" if len(one_line) > length else one_line


def node_label(node: Node) -> str:
    """A short human label for a node, for the block-picker select."""
    k = kind(node)
    if k == "text":
        return f"Text: {_preview(node.get('content', '')) or '(empty)'}"
    if k == "container":
        return f"Container ({len(node.get('components', []))} items)"
    if k == "section":
        acc = node.get("accessory")
        acc_label = kind(acc) if acc else "no accessory"
        return f"Section ({len(node.get('components', []))} text, {acc_label})"
    if k == "media":
        return f"Media gallery ({len(node.get('items', []))} images)"
    if k == "separator":
        return "Separator" + ("" if node.get("divider", True) else " (no divider)")
    if k == "link_button":
        label = _preview(_button_of(node).get("label", "")) or "(empty)"
        return f"Link button: {label}"
    if k == "file":
        return "File (from original post)"
    return "Unknown component"


# --- preview sanitisation ---------------------------------------------------------

# A section accessory the preview can fall back on if none is set yet: a tiny 1px
# transparent placeholder would need a URL, so instead an incomplete section is rendered
# as a plain placeholder text (see ``_sanitize_node``).


def _placeholder(message: str) -> Node:
    return {"type": TEXT_DISPLAY, "content": f"-# ⚠️ {message}"}


def _accessory_ok(accessory: Node) -> bool:
    k = kind(accessory)
    if k == "thumbnail":
        return bool(accessory.get("media", {}).get("url"))
    if k == "link_button":
        button = _button_of(accessory)
        return bool(button.get("label") and button.get("url"))
    return False


def sanitize_for_preview(nodes: list[Node]) -> list[Node]:
    """Return a deep copy of ``nodes`` that is always valid to send to Discord.

    Mid-construction states (an empty container, a section without an accessory, an
    empty text block, …) would make Discord reject the live-preview edit. Each such node
    is downgraded to a placeholder text so the preview never breaks, while the real
    (possibly incomplete) nodes stay in the builder's state.
    """
    return [_sanitize_node(node) for node in nodes]


def _sanitize_node(node: Node) -> Node:
    k = kind(node)
    if k == "container":
        children = [_sanitize_node(child) for child in node.get("components", [])]
        if not children:
            children = [_placeholder("empty container — open it to add blocks")]
        return {**node, "components": children}
    if k == "section":
        texts = node.get("components", [])
        accessory = node.get("accessory")
        if not texts or not accessory or not _accessory_ok(accessory):
            return _placeholder("section — add 1–3 text blocks and an accessory")
        good_texts = [t for t in texts if (t.get("content") or "").strip()]
        if not good_texts:
            return _placeholder("section — add some text")
        return {**node, "components": good_texts, "accessory": accessory}
    if k == "text":
        if not (node.get("content") or "").strip():
            return _placeholder("empty text block")
        return node
    if k == "media":
        items = [i for i in node.get("items", []) if i.get("media", {}).get("url")]
        if not items:
            return _placeholder("empty media gallery")
        return {**node, "items": items}
    if k == "link_button":
        button = _button_of(node)
        if not (button.get("label") and button.get("url")):
            return _placeholder("incomplete link button")
        return node
    return node


# --- validation -------------------------------------------------------------------


def validate(nodes: list[Node]) -> list[str]:
    """Return human-readable problems that would make the message invalid to send."""
    problems: list[str] = []
    if not nodes:
        problems.append("The message is empty — add at least one block.")
    if len(nodes) > _MAX_TOP_LEVEL:
        problems.append(
            f"Too many top-level blocks ({len(nodes)}); Discord allows "
            f"{_MAX_TOP_LEVEL}. Group some inside a container."
        )
    for node in nodes:
        _validate_node(node, problems)
    return problems


def _validate_node(node: Node, problems: list[str]) -> None:
    k = kind(node)
    if k == "container":
        children = node.get("components", [])
        if not children:
            problems.append("A container is empty — add a block inside or delete it.")
        for child in children:
            _validate_node(child, problems)
    elif k == "section":
        texts = node.get("components", [])
        if not (1 <= len(texts) <= _MAX_SECTION_TEXTS):
            problems.append(
                f"A section must have 1–{_MAX_SECTION_TEXTS} text blocks "
                f"(it has {len(texts)})."
            )
        if not node.get("accessory"):
            problems.append("A section is missing its accessory (thumbnail or button).")
    elif k == "text":
        if not (node.get("content") or "").strip():
            problems.append("A text block is empty.")
    elif k == "media":
        if not node.get("items"):
            problems.append("A media gallery has no images.")
    elif k == "link_button":
        button = _button_of(node)
        if not (button.get("label") and button.get("url")):
            problems.append("A link button needs both a label and a URL.")
