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

"""Render a captured mirror-message snapshot (see :class:`MirrorMessageVersion`) to a
safe HTML string for the web mirror-log render pane.

The snapshot payload is Discord's own component/embed JSON, in one of two shapes:

- ``kind == "cv2"``:  a Components V2 tree — ``{"components": [raw node dicts]}``.
- ``kind == "classic"``:  ``{"content": str, "embeds": [embed dicts]}``.

This is the CV2-tree walker the design doc's Phase D2 called for, kept deliberately in
one module with a strict *"known node kinds → labeled placeholder"* degrade contract so
it can't sprawl. It reuses ``hybrid_post_core``'s battle-tested inline-markdown / emoji
/ URL-whitelist leaf renderers (every text leaf escaped, masked-link + media + button
URLs ``http(s)``-validated, only the ``{span, strong, em, a, img}`` tag whitelist plus
the container/section wrappers here), so the output is safe for the ``box.innerHTML``
sink the page injects it through.

The **diff** view (:func:`render_diff`) renders the whole message in place and
highlights what changed since the previous version, both **component-by-component** (a
whole added/removed component is wrapped green/red) and **word-by-word** (text edits
show inline ``<ins>``/``<del>`` marks). It is a recursive structural diff: sibling
components are aligned, recursing into containers/sections, so an edit is localised
rather than blowing away the whole message.

Emoji: captured content carries full ``<:name:id>`` / ``<a:name:id>`` custom emoji, so
we resolve straight to the Discord CDN (``cdn.discordapp.com/emojis/{id}.{png|gif}``) —
no bot or guild-emoji dict needed, unlike the live post previewer.
"""

import difflib
import html
import json
import re
import typing as t

from .hybrid_post_core import _normalize_heading_spacing, _render_inline, _render_line

# Discord component type ints (mirrors dd.anchor.cv2_nodes; duplicated so this renderer
# stays a leaf module with no cfg/DB import weight).
_ACTION_ROW = 1
_BUTTON = 2
_SECTION = 9
_TEXT_DISPLAY = 10
_THUMBNAIL = 11
_MEDIA_GALLERY = 12
_FILE = 13
_SEPARATOR = 14
_CONTAINER = 17

EmojiSub = t.Callable[[t.Any], str]


def _cdn_emoji_substituter(match: t.Any) -> str:
    """A ``re_user_side_emoji`` substituter that resolves custom emoji to CDN ``<img>``.

    Group shape (``(<a?)?:(\\w+)(~\\d)*:(\\d+>)?``): group 1 is the ``<``/``<a`` prefix,
    group 2 the name, group 4 the ``id>``. A custom emoji (id present) becomes an
    ``<img>`` off the Discord CDN (``.gif`` when animated); a bare ``:name:`` with no id
    can't be resolved without a guild dict, so it renders as its escaped text.
    """
    prefix = match.group(1) or ""
    name = str(match.group(2) or "")
    id_group = match.group(4)  # e.g. "123456789>" or None
    if id_group:
        emoji_id = id_group.rstrip(">")
        if emoji_id.isdigit():
            ext = "gif" if prefix == "<a" else "png"
            src = html.escape(
                f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}", quote=True
            )
            alt = html.escape(name, quote=True)
            return f'<img class="emoji" src="{src}" alt=":{alt}:">'
    return html.escape(match.group(0))


def _is_http_url(value: t.Any) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def _media_url(media: t.Any) -> str | None:
    """The ``http(s)`` url inside a ``{"url": ...}`` media object, else ``None``."""
    if isinstance(media, dict) and _is_http_url(media.get("url")):
        return str(media["url"])
    return None


def _accent_style(color: t.Any) -> str:
    """A ``style`` attr fragment painting the left accent bar, for an int colour."""
    if isinstance(color, bool) or not isinstance(color, int):
        return ""
    return f' style="border-left-color:#{color & 0xFFFFFF:06x}"'


def _placeholder(message: str) -> str:
    """A labeled degrade box for an unknown / unrenderable node (strict fallback)."""
    return f'<div class="cv2-placeholder">⚠️ {html.escape(message)}</div>'


def _render_markdown(content: str, sub: EmojiSub) -> str:
    """A text leaf's inner HTML: per-line heading/bullet markdown, inline-safe."""
    lines = _normalize_heading_spacing(content.split("\n"))
    return "\n".join(_render_line(line, sub) for line in lines)


# --- plain render --------------------------------------------------------------------


def _text_block(content: t.Any, sub: EmojiSub) -> str:
    """A text-display body (or embed description / field value), pre-wrapped."""
    return f'<div class="cv2-text">{_render_markdown(str(content), sub)}</div>'


def _emoji_prefix_html(emoji: t.Any) -> str:
    """Leading ``<img>``/text for a button's emoji object (``{name,id,animated}``)."""
    if not isinstance(emoji, dict):
        return ""
    emoji_id = emoji.get("id")
    name = str(emoji.get("name") or "")
    if emoji_id and str(emoji_id).isdigit():
        ext = "gif" if emoji.get("animated") else "png"
        src = html.escape(
            f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}", quote=True
        )
        return (
            f'<img class="emoji" src="{src}" alt=":{html.escape(name, quote=True)}:"> '
        )
    return (html.escape(name) + " ") if name else ""


def _render_button(node: t.Any) -> str:
    """A link button → anchor button. Non-link / url-less buttons (e.g. interactive
    ones that never survive a mirror) are dropped, matching the send whitelist."""
    if not isinstance(node, dict) or not _is_http_url(node.get("url")):
        return ""
    href = html.escape(str(node["url"]), quote=True)
    label = html.escape(str(node.get("label") or ""))
    return (
        f'<a class="cv2-button" href="{href}" target="_blank" '
        'rel="noopener noreferrer">'
        f"{_emoji_prefix_html(node.get('emoji'))}{label}</a>"
    )


def _render_thumbnail(node: t.Any) -> str:
    url = _media_url(node.get("media")) if isinstance(node, dict) else None
    if not url:
        return ""
    alt = html.escape(str(node.get("description") or "thumbnail"), quote=True)
    spoiler = " cv2-spoiler" if node.get("spoiler") else ""
    return (
        f'<img class="cv2-thumb{spoiler}" src="{html.escape(url, quote=True)}" '
        f'alt="{alt}">'
    )


def _render_accessory(node: t.Any) -> str:
    """A section accessory is a thumbnail or a (link) button."""
    if not isinstance(node, dict):
        return ""
    ty = node.get("type")
    if ty == _THUMBNAIL:
        thumb = _render_thumbnail(node)
        return f'<div class="cv2-accessory">{thumb}</div>' if thumb else ""
    if ty == _BUTTON:
        btn = _render_button(node)
        return f'<div class="cv2-accessory">{btn}</div>' if btn else ""
    return ""


def _render_section(node: t.Any, sub: EmojiSub) -> str:
    body = "".join(_render_node(c, sub) for c in node.get("components") or [])
    accessory = _render_accessory(node.get("accessory"))
    return (
        '<div class="cv2-section">'
        f'<div class="cv2-section-body">{body}</div>{accessory}</div>'
    )


def _render_media(node: t.Any) -> str:
    """A media gallery → a Discord-style image grid; each tile links to the full image.

    The item count drives the grid layout (1 / 2 / 3 / 4 / many) via a CSS class, so the
    tiles split the way Discord's galleries do rather than stacking full-width.

    Per-item ``description`` becomes the ``alt`` (Discord's alt text — the only thing a
    screen reader has to go on for an image-only post) and ``spoiler`` adds the blur
    class, so the render matches what Discord will show rather than quietly dropping
    both."""
    items: list[tuple[str, str, bool]] = []
    for item in node.get("items") or []:
        if not isinstance(item, dict):
            continue
        url = _media_url(item.get("media"))
        if url:
            items.append(
                (url, str(item.get("description") or ""), bool(item.get("spoiler")))
            )
    if not items:
        return ""
    layout = {1: "n1", 2: "n2", 3: "n3", 4: "n4"}.get(len(items), "many")
    tiles = "".join(
        f'<a class="cv2-media-item{" cv2-spoiler" if spoiler else ""}" '
        f'href="{html.escape(url, quote=True)}" '
        'target="_blank" rel="noopener noreferrer">'
        f'<img src="{html.escape(url, quote=True)}" '
        f'alt="{html.escape(alt, quote=True)}" loading="lazy"></a>'
        for url, alt, spoiler in items
    )
    return f'<div class="cv2-media {layout}">{tiles}</div>'


def _render_separator(node: t.Any) -> str:
    return (
        '<hr class="cv2-sep">'
        if node.get("divider", True)
        else '<div class="cv2-spacer"></div>'
    )


def _render_action_row(node: t.Any) -> str:
    buttons = "".join(_render_button(c) for c in node.get("components") or [])
    return f'<div class="cv2-buttons">{buttons}</div>' if buttons else ""


def _render_container(node: t.Any, sub: EmojiSub) -> str:
    inner = "".join(_render_node(c, sub) for c in node.get("components") or [])
    accent = _accent_style(node.get("accent_color"))
    return f'<div class="cv2-container"{accent}>{inner}</div>'


def _render_node(node: t.Any, sub: EmojiSub) -> str:
    """One CV2 node → HTML, degrading unknown kinds to a labeled placeholder."""
    if not isinstance(node, dict):
        return ""
    ty = node.get("type")
    if ty == _CONTAINER:
        return _render_container(node, sub)
    if ty == _TEXT_DISPLAY:
        return _text_block(node.get("content", ""), sub)
    if ty == _SECTION:
        return _render_section(node, sub)
    if ty == _MEDIA_GALLERY:
        return _render_media(node)
    if ty == _SEPARATOR:
        return _render_separator(node)
    if ty == _THUMBNAIL:
        return _render_thumbnail(node)
    if ty == _ACTION_ROW:
        return _render_action_row(node)
    if ty == _BUTTON:
        return _render_button(node)
    if ty == _FILE:
        return _placeholder("File attachment (from the original post)")
    return _placeholder(f"Unsupported component (type {ty})")


def _render_embed(embed: dict, sub: EmojiSub) -> str:
    """A minimal embed card — title, description, fields, image, footer.

    Classic messages are the rare case here (CV2 is the mirror-feed norm), so this keeps
    to structure over pixel-fidelity, per the design doc."""
    parts: list[str] = []
    author = embed.get("author")
    if isinstance(author, dict) and author.get("name"):
        parts.append(
            f'<div class="embed-author">{html.escape(str(author["name"]))}</div>'
        )
    if embed.get("title"):
        parts.append(
            f'<div class="embed-title">{_render_inline(str(embed["title"]), sub)}</div>'
        )
    if embed.get("description"):
        parts.append(
            f'<div class="embed-desc">{_text_block(embed["description"], sub)}</div>'
        )
    for field in embed.get("fields") or []:
        if not isinstance(field, dict):
            continue
        fp = []
        if field.get("name"):
            fp.append(
                f'<div class="embed-field-name">'
                f"{_render_inline(str(field['name']), sub)}</div>"
            )
        if field.get("value"):
            fp.append(
                '<div class="embed-field-value">'
                f"{_text_block(field['value'], sub)}</div>"
            )
        if fp:
            parts.append(f'<div class="embed-field">{"".join(fp)}</div>')
    image_url = _media_url(embed.get("image")) or _media_url(embed.get("thumbnail"))
    if image_url:
        parts.append(
            f'<img class="embed-image" src="{html.escape(image_url, quote=True)}" '
            'alt="embed image">'
        )
    footer = embed.get("footer")
    if isinstance(footer, dict) and footer.get("text"):
        parts.append(
            f'<div class="embed-footer">{html.escape(str(footer["text"]))}</div>'
        )
    accent = _accent_style(embed.get("color"))
    return f'<div class="cv2-embed"{accent}>{"".join(parts)}</div>'


def _render_classic(payload: dict, sub: EmojiSub) -> str:
    content = str(payload.get("content") or "")
    embeds = [e for e in (payload.get("embeds") or []) if isinstance(e, dict)]
    # Per the design doc, classic renders are minimal — note what's present rather than
    # chasing embed pixel-fidelity.
    bits = [
        "text" if content.strip() else None,
        f"{len(embeds)} embed(s)" if embeds else None,
    ]
    note = " · ".join(b for b in bits if b) or "empty message"
    parts = [f'<div class="cv2-note">Classic message — {html.escape(note)}</div>']
    if content.strip():
        parts.append(_text_block(content, sub))
    parts.extend(_render_embed(e, sub) for e in embeds)
    return '<div class="cv2-root classic">' + "".join(parts) + "</div>"


def render_snapshot(
    payload: dict[str, t.Any] | None,
    kind: str,
    *,
    emoji_sub: EmojiSub | None = None,
) -> str:
    """Render a stored snapshot payload to safe HTML.

    ``kind`` selects the branch; a truncated (over-cap) payload degrades to a note. The
    result is a trusted, pre-escaped HTML string for the page's ``innerHTML`` sink."""
    sub = emoji_sub or _cdn_emoji_substituter
    if not isinstance(payload, dict) or payload.get("truncated"):
        return _placeholder("This version's snapshot was too large to store in full.")
    if kind == "cv2":
        nodes = payload.get("components") or []
        body = "".join(_render_node(n, sub) for n in nodes)
        return (
            f'<div class="cv2-root">{body}</div>'
            if body
            else _placeholder("This version captured no renderable components.")
        )
    return _render_classic(payload, sub)


# --- diff (Phase E) ------------------------------------------------------------------
#
# A recursive structural diff of the new vs old snapshot. Sibling components are aligned
# by exact serialization (difflib); unmatched components are wrapped whole as added
# (green) or removed (red); a matched-but-changed container/section recurses, and a
# changed text leaf gets an inline word-level diff — so edits are localised and shown
# both component-by-component and word-by-word.

_WORD = re.compile(r"\S+|\s+")


def _node_key(node: t.Any) -> str:
    """A stable serialization of a node, for exact-match alignment."""
    return json.dumps(node, sort_keys=True, ensure_ascii=False, default=str)


def _wrap(cls: str, inner: str) -> str:
    return f'<div class="{cls}">{inner}</div>'


def _added_node(node: t.Any, sub: EmojiSub) -> str:
    return _wrap("cv2-added", _render_node(node, sub))


def _removed_node(node: t.Any, sub: EmojiSub) -> str:
    return _wrap("cv2-removed", _render_node(node, sub))


def _word_diff_html(old: str, new: str) -> str:
    """Word-level inline diff of two single lines → ``<ins>``/``<del>``-marked HTML."""
    old_tokens = _WORD.findall(old)
    new_tokens = _WORD.findall(new)
    sm = difflib.SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)
    out: list[str] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        old_seg = html.escape("".join(old_tokens[i1:i2]))
        new_seg = html.escape("".join(new_tokens[j1:j2]))
        if op == "equal":
            out.append(new_seg)
        elif op == "delete":
            out.append(f"<del>{old_seg}</del>")
        elif op == "insert":
            out.append(f"<ins>{new_seg}</ins>")
        else:  # replace
            out.append(f"<del>{old_seg}</del><ins>{new_seg}</ins>")
    return "".join(out)


def _line_diff_html(old: str, new: str, sub: EmojiSub) -> str:
    """Diff two multi-line text leaves line-by-line: unchanged lines keep their markdown
    rendering; changed lines show a raw word-level ``<ins>``/``<del>`` diff.

    Diffing per line (not over the whole leaf) means a one-word edit in a big
    text_display doesn't drop heading/bold rendering for every *other* line."""
    old_lines = old.split("\n")
    new_lines = new.split("\n")
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    out: list[str] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            out.extend(_render_line(line, sub) for line in new_lines[j1:j2])
        elif op == "delete":
            out.extend(f"<del>{html.escape(line)}</del>" for line in old_lines[i1:i2])
        elif op == "insert":
            out.extend(
                f"<ins>{_render_line(line, sub)}</ins>" for line in new_lines[j1:j2]
            )
        else:  # replace: word-diff the changed block (raw text, inline marks)
            out.append(
                _word_diff_html(
                    "\n".join(old_lines[i1:i2]), "\n".join(new_lines[j1:j2])
                )
            )
    return "\n".join(out)


def _diff_accessory(old: t.Any, new: t.Any, sub: EmojiSub) -> str:
    """Diff a section accessory: added green, removed red, changed shows both."""
    new_ok = isinstance(new, dict) and _render_accessory(new)
    old_ok = isinstance(old, dict) and _render_accessory(old)
    if not new_ok:
        return _wrap("cv2-removed", _render_accessory(old)) if old_ok else ""
    if not old_ok:
        return _wrap("cv2-added", _render_accessory(new))
    if _node_key(old) != _node_key(new):
        return _wrap("cv2-removed", _render_accessory(old)) + _wrap(
            "cv2-added", _render_accessory(new)
        )
    return _render_accessory(new)


def _diff_pair(old: t.Any, new: t.Any, sub: EmojiSub) -> str:
    """Two same-position, differing components → localised diff HTML."""
    if not (isinstance(old, dict) and isinstance(new, dict)):
        return _added_node(new, sub)
    to, tn = old.get("type"), new.get("type")
    if to != tn:  # a component replaced by a different kind: remove old, add new
        return _removed_node(old, sub) + _added_node(new, sub)
    if tn == _TEXT_DISPLAY:
        oc, nc = str(old.get("content", "")), str(new.get("content", ""))
        body = _render_markdown(nc, sub) if oc == nc else _line_diff_html(oc, nc, sub)
        return f'<div class="cv2-text">{body}</div>'
    if tn == _CONTAINER:
        inner = _diff_nodes(
            old.get("components") or [], new.get("components") or [], sub
        )
        accent = _accent_style(new.get("accent_color"))
        return f'<div class="cv2-container"{accent}>{inner}</div>'
    if tn == _SECTION:
        inner = _diff_nodes(
            old.get("components") or [], new.get("components") or [], sub
        )
        acc = _diff_accessory(old.get("accessory"), new.get("accessory"), sub)
        return (
            '<div class="cv2-section">'
            f'<div class="cv2-section-body">{inner}</div>{acc}</div>'
        )
    if tn == _ACTION_ROW:
        inner = _diff_nodes(
            old.get("components") or [], new.get("components") or [], sub
        )
        return f'<div class="cv2-buttons">{inner}</div>'
    # A changed leaf (media / thumbnail / separator / …): show old removed + new added.
    return _removed_node(old, sub) + _added_node(new, sub)


def _diff_replace(old_block: list, new_block: list, sub: EmojiSub) -> str:
    """A difflib 'replace' run: pair components positionally (diffing each pair), and
    tail extras become pure add/remove."""
    out: list[str] = []
    for k in range(max(len(old_block), len(new_block))):
        o = old_block[k] if k < len(old_block) else None
        n = new_block[k] if k < len(new_block) else None
        if o is None:
            out.append(_added_node(n, sub))
        elif n is None:
            out.append(_removed_node(o, sub))
        else:
            out.append(_diff_pair(o, n, sub))
    return "".join(out)


def _diff_nodes(old_nodes: list, new_nodes: list, sub: EmojiSub) -> str:
    """Diff two sibling-component lists: equal runs render plain, added/removed runs are
    wrapped green/red, and replaced runs recurse per :func:`_diff_replace`."""
    a = [_node_key(n) for n in old_nodes]
    b = [_node_key(n) for n in new_nodes]
    out: list[str] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(
        a=a, b=b, autojunk=False
    ).get_opcodes():
        if op == "equal":
            out.extend(_render_node(n, sub) for n in new_nodes[j1:j2])
        elif op == "delete":
            out.extend(_removed_node(n, sub) for n in old_nodes[i1:i2])
        elif op == "insert":
            out.extend(_added_node(n, sub) for n in new_nodes[j1:j2])
        else:
            out.append(_diff_replace(old_nodes[i1:i2], new_nodes[j1:j2], sub))
    return "".join(out)


def _diff_embeds(old_embeds: list, new_embeds: list, sub: EmojiSub) -> str:
    """Classic embeds diff: unchanged plain, added green, removed red, changed both
    (whole-embed granularity — classic-with-embeds edits are rare)."""
    a = [_node_key(e) for e in old_embeds]
    b = [_node_key(e) for e in new_embeds]
    out: list[str] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(
        a=a, b=b, autojunk=False
    ).get_opcodes():
        if op == "equal":
            out.extend(_render_embed(e, sub) for e in new_embeds[j1:j2])
        else:
            out.extend(
                _wrap("cv2-removed", _render_embed(e, sub)) for e in old_embeds[i1:i2]
            )
            out.extend(
                _wrap("cv2-added", _render_embed(e, sub)) for e in new_embeds[j1:j2]
            )
    return "".join(out)


def _diff_classic(new_payload: dict, old_payload: dict, sub: EmojiSub) -> str:
    oc = str(old_payload.get("content") or "")
    nc = str(new_payload.get("content") or "")
    parts = []
    if nc.strip() or oc.strip():
        body = _render_markdown(nc, sub) if oc == nc else _line_diff_html(oc, nc, sub)
        parts.append(f'<div class="cv2-text">{body}</div>')
    old_embeds = [e for e in (old_payload.get("embeds") or []) if isinstance(e, dict)]
    new_embeds = [e for e in (new_payload.get("embeds") or []) if isinstance(e, dict)]
    if old_embeds or new_embeds:
        parts.append(_diff_embeds(old_embeds, new_embeds, sub))
    return f'<div class="cv2-root classic">{"".join(parts)}</div>'


def render_diff(
    new_payload: dict[str, t.Any] | None,
    new_kind: str,
    old_payload: dict[str, t.Any] | None,
    old_kind: str,
) -> str:
    """Render the ``new`` version in full, highlighting what changed vs ``old``.

    Component-by-component (a whole added component is wrapped green, a removed one red)
    and word-by-word (text edits show inline ``<ins>``/``<del>``). The whole message —
    with its structure, images and buttons — is always shown; a no-change diff renders
    the message with a small note. A truncated snapshot on either side degrades to a
    note; a version that switched message format falls back to a plain render."""
    if (isinstance(new_payload, dict) and new_payload.get("truncated")) or (
        isinstance(old_payload, dict) and old_payload.get("truncated")
    ):
        return _placeholder("Cannot diff — a version's snapshot was stored truncated.")
    sub = _cdn_emoji_substituter
    new_payload = new_payload or {}
    old_payload = old_payload or {}
    if new_kind != old_kind:
        note = (
            '<div class="cv2-note">Message format changed since the previous '
            "version — showing the current version.</div>"
        )
        return note + render_snapshot(new_payload, new_kind)

    if new_kind == "cv2":
        old_nodes = old_payload.get("components") or []
        new_nodes = new_payload.get("components") or []
        unchanged = _node_key(old_nodes) == _node_key(new_nodes)
        body = _diff_nodes(old_nodes, new_nodes, sub)
        inner = (
            f'<div class="cv2-root">{body}</div>'
            if body
            else _placeholder("This version captured no renderable components.")
        )
    else:
        unchanged = _node_key(old_payload) == _node_key(new_payload)
        inner = _diff_classic(new_payload, old_payload, sub)

    note = (
        '<div class="cv2-note">No changes from the previous version.</div>'
        if unchanged
        else ""
    )
    return note + inner
