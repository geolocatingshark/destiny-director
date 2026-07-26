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

Emoji: captured content carries full ``<:name:id>`` / ``<a:name:id>`` custom emoji, so
we resolve straight to the Discord CDN (``cdn.discordapp.com/emojis/{id}.{png|gif}``) —
no bot or guild-emoji dict needed, unlike the live post previewer.
"""

import difflib
import html
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


def _text_block(content: t.Any, sub: EmojiSub) -> str:
    """A text-display body: per-line heading/bullet markdown, inline-safe + pre-wrap."""
    lines = _normalize_heading_spacing(str(content).split("\n"))
    return (
        '<div class="cv2-text">'
        + "\n".join(_render_line(line, sub) for line in lines)
        + "</div>"
    )


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


def _render_button(node: t.Any, sub: EmojiSub) -> str:
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
    return (
        f'<img class="cv2-thumb" src="{html.escape(url, quote=True)}" alt="thumbnail">'
    )


def _render_accessory(node: t.Any, sub: EmojiSub) -> str:
    """A section accessory is a thumbnail or a (link) button."""
    if not isinstance(node, dict):
        return ""
    ty = node.get("type")
    if ty == _THUMBNAIL:
        thumb = _render_thumbnail(node)
        return f'<div class="cv2-accessory">{thumb}</div>' if thumb else ""
    if ty == _BUTTON:
        btn = _render_button(node, sub)
        return f'<div class="cv2-accessory">{btn}</div>' if btn else ""
    return ""


def _render_section(node: t.Any, sub: EmojiSub) -> str:
    body = "".join(_render_node(c, sub) for c in node.get("components") or [])
    accessory = _render_accessory(node.get("accessory"), sub)
    return (
        '<div class="cv2-section">'
        f'<div class="cv2-section-body">{body}</div>{accessory}</div>'
    )


def _render_media(node: t.Any, sub: EmojiSub) -> str:
    items = []
    for item in node.get("items") or []:
        url = _media_url(item.get("media")) if isinstance(item, dict) else None
        if url:
            items.append(
                f'<img class="cv2-media-item" src="{html.escape(url, quote=True)}" '
                'alt="image">'
            )
    return f'<div class="cv2-media">{"".join(items)}</div>' if items else ""


def _render_separator(node: t.Any, sub: EmojiSub) -> str:
    return (
        '<hr class="cv2-sep">'
        if node.get("divider", True)
        else '<div class="cv2-spacer"></div>'
    )


def _render_action_row(node: t.Any, sub: EmojiSub) -> str:
    buttons = "".join(_render_button(c, sub) for c in node.get("components") or [])
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
        return _render_media(node, sub)
    if ty == _SEPARATOR:
        return _render_separator(node, sub)
    if ty == _THUMBNAIL:
        return _render_thumbnail(node)
    if ty == _ACTION_ROW:
        return _render_action_row(node, sub)
    if ty == _BUTTON:
        return _render_button(node, sub)
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
                f'{_text_block(field["value"], sub)}</div>'
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
# Announcement edits almost always rework *text* inside a stable structure, so the diff
# highlights the text word-for-word and summarises any structural (image/button/embed)
# change as a one-line note above it. This keeps the diff robust to tree reshaping (a
# node-position aligner would mis-anchor when a component is inserted) and cheaply,
# thoroughly testable — the design doc's bounded "cap to text-bearing nodes".

_WORD = re.compile(r"\S+|\s+")


def _walk_text_units(nodes: t.Iterable[t.Any], out: list[str]) -> None:
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("type") == _TEXT_DISPLAY:
            content = str(node.get("content", ""))
            if content.strip():
                out.append(content)
        for key in ("components",):
            child = node.get(key)
            if isinstance(child, list):
                _walk_text_units(child, out)


def _extract_text_units(payload: t.Any, kind: str) -> list[str]:
    """Ordered text blocks of a snapshot — the diffable, text-bearing leaves only."""
    if not isinstance(payload, dict) or payload.get("truncated"):
        return []
    if kind == "cv2":
        out: list[str] = []
        _walk_text_units(payload.get("components") or [], out)
        return out
    out = []
    content = str(payload.get("content") or "")
    if content.strip():
        out.append(content)
    for embed in payload.get("embeds") or []:
        if not isinstance(embed, dict):
            continue
        for key in ("title", "description"):
            if embed.get(key):
                out.append(str(embed[key]))
        for field in embed.get("fields") or []:
            if isinstance(field, dict):
                for key in ("name", "value"):
                    if field.get(key):
                        out.append(str(field[key]))
    return out


def _count_media(payload: t.Any, kind: str) -> tuple[int, int]:
    """(image count, button count) of a snapshot, for the structural-change note."""
    images = buttons = 0
    if not isinstance(payload, dict) or payload.get("truncated"):
        return (0, 0)
    stack: list[t.Any] = list(payload.get("components") or [])
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        ty = node.get("type")
        if ty == _MEDIA_GALLERY:
            images += len(
                [
                    i
                    for i in node.get("items") or []
                    if _media_url(i.get("media") if isinstance(i, dict) else None)
                ]
            )
        elif ty == _THUMBNAIL and _media_url(node.get("media")):
            images += 1
        elif ty == _BUTTON and _is_http_url(node.get("url")):
            buttons += 1
        for key in ("components", "items"):
            child = node.get(key)
            if isinstance(child, list):
                stack.extend(child)
        accessory = node.get("accessory")
        if isinstance(accessory, dict):
            stack.append(accessory)
    if kind == "classic":
        for embed in payload.get("embeds") or []:
            if isinstance(embed, dict) and (
                _media_url(embed.get("image")) or _media_url(embed.get("thumbnail"))
            ):
                images += 1
    return images, buttons


def _word_diff_html(old: str, new: str) -> str:
    """Word-level inline diff of two text blocks → ``<ins>``/``<del>``-marked HTML."""
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


def _structural_note(old_payload, old_kind, new_payload, new_kind) -> str:
    old_imgs, old_btns = _count_media(old_payload, old_kind)
    new_imgs, new_btns = _count_media(new_payload, new_kind)
    notes: list[str] = []
    for label, old_n, new_n in (
        ("image", old_imgs, new_imgs),
        ("button", old_btns, new_btns),
    ):
        if new_n > old_n:
            notes.append(f"+{new_n - old_n} {label}{'s' if new_n - old_n != 1 else ''}")
        elif old_n > new_n:
            notes.append(f"−{old_n - new_n} {label}{'s' if old_n - new_n != 1 else ''}")
    if not notes:
        return ""
    summary = html.escape(", ".join(notes))
    return f'<div class="cv2-note">Structural change: {summary}</div>'


def render_diff(
    new_payload: dict[str, t.Any] | None,
    new_kind: str,
    old_payload: dict[str, t.Any] | None,
    old_kind: str,
) -> str:
    """Render ``new`` vs ``old`` as a word-level text diff + a structural-change note.

    Green ``<ins>`` = added, struck red ``<del>`` = removed. Text-bearing leaves are
    concatenated in document order and diffed; image/button count deltas are summarised
    above. A truncated snapshot on either side degrades to a note."""
    if (isinstance(new_payload, dict) and new_payload.get("truncated")) or (
        isinstance(old_payload, dict) and old_payload.get("truncated")
    ):
        return _placeholder("Cannot diff — a version's snapshot was stored truncated.")
    old_text = "\n\n".join(_extract_text_units(old_payload, old_kind))
    new_text = "\n\n".join(_extract_text_units(new_payload, new_kind))
    note = _structural_note(old_payload, old_kind, new_payload, new_kind)
    if old_text == new_text and not note:
        return (
            '<div class="cv2-note">No text or structural changes between these '
            "versions.</div>"
        )
    diff_html = _word_diff_html(old_text, new_text)
    return f'{note}<div class="cv2-diff cv2-text">{diff_html}</div>'
