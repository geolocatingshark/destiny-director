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

"""What changed between two captured versions of a mirrored message.

The mirror log shows one snapshot (see :class:`MirrorMessageVersion`) against the one
before it. Payloads are Discord's own component/embed JSON, in one of two shapes:

- ``kind == "cv2"``:  a Components V2 tree — ``{"components": [raw node dicts]}``.
- ``kind == "classic"``:  ``{"content": str, "embeds": [embed dicts]}``.

**This module does not render.** It used to — it was the CV2-tree → safe-HTML walker —
but there is one renderer now, ``web_static/cv2_render.js``, and it is the client's
(see ``docs/architecture.md``, "Rendering a message on the web"). What stayed here is
the half that needs :mod:`difflib`: aligning two trees and annotating what moved.

:func:`diff_payload` is the whole public surface. It returns the *new* tree carrying
three annotations the shared renderer knows how to draw — ``_mark`` on a whole node,
``_lines`` on a changed text leaf, and an ``accessory`` that may become a list — so an
edit shows up localised, component-by-component and word-by-word, rather than blowing
away the whole message.

Everything it emits is **pre-split**: line runs and word runs are decided here, so the
browser only draws. That is deliberate. This content came from someone else's server,
and the less it is reasoned about client-side the smaller the surface that reasoning
can be wrong about.
"""

import difflib
import json
import re
import typing as t

# Discord component type ints (mirrors dd.anchor.cv2_nodes; duplicated so this module
# stays a leaf with no cfg/DB import weight). Only the kinds the alignment treats
# specially are named — the rest are compared whole, by key, and never inspected.
_ACTION_ROW = 1
_BUTTON = 2
_SECTION = 9
_TEXT_DISPLAY = 10
_THUMBNAIL = 11
_CONTAINER = 17


def _is_http_url(value: t.Any) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def _media_url(media: t.Any) -> str | None:
    """The ``http(s)`` url inside a ``{"url": ...}`` media object, else ``None``."""
    if isinstance(media, dict) and _is_http_url(media.get("url")):
        return str(media["url"])
    return None


#: One whitespace-preserving token, so re-joining a run list reproduces the spacing.
_WORD = re.compile(r"\S+|\s+")


def _node_key(node: t.Any) -> str:
    """A stable serialization of a node, for exact-match alignment."""
    return json.dumps(node, sort_keys=True, ensure_ascii=False, default=str)


# --- diff, as annotations ------------------------------------------------------------
#
# The same recursive alignment as above, emitting DATA rather than markup: the new tree
# with three optional annotations, which the shared client renderer knows how to draw.
#
#   ``_mark``      "added" / "removed" on any node — wrap it green / red
#   ``_lines``     replaces ``content`` on a changed text display; see _diff_lines
#   ``accessory``  may become a *list*, for the three-state old-and-new case
#
# Why the split: aligning two trees needs ``difflib`` and belongs here, but drawing the
# result is the renderer's job, and there is now exactly one renderer
# (``web_static/cv2_render.js``). Keeping the alignment in Python and shipping its
# verdict is what lets the diff survive that move without a second implementation.
#
# What matters most is that the client only ever *draws* — every segment below is
# pre-split here, so no diffing happens on untrusted content in the browser and every
# piece of text lands in a field the renderer escapes.


def _annotate(node: t.Any, how: str) -> t.Any:
    """Tag a whole node as added or removed."""
    return {**node, "_mark": how} if isinstance(node, dict) else node


def _word_runs(old: str, new: str) -> list[dict[str, str]]:
    """Word-level runs for a changed block, as ``{op, text}`` in reading order.

    Whitespace runs are their own tokens (``_WORD``), so re-joining the ``text`` values
    reproduces the original spacing exactly.
    """
    old_tokens = _WORD.findall(old)
    new_tokens = _WORD.findall(new)
    sm = difflib.SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)
    runs: list[dict[str, str]] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        old_seg = "".join(old_tokens[i1:i2])
        new_seg = "".join(new_tokens[j1:j2])
        if op == "equal":
            runs.append({"op": "equal", "text": new_seg})
        elif op == "delete":
            runs.append({"op": "del", "text": old_seg})
        elif op == "insert":
            runs.append({"op": "ins", "text": new_seg})
        else:
            runs.append({"op": "del", "text": old_seg})
            runs.append({"op": "ins", "text": new_seg})
    return runs


def _diff_lines(old: str, new: str) -> list[dict[str, t.Any]]:
    """Line-level annotation of a changed text leaf.

    Per line, not over the whole leaf, so a one-word edit in a long text display does
    not drop heading/bold rendering for every *other* line. The four cases carry
    different rendering rules, and the renderer needs to be told which is which:

    - ``equal`` — render the line's markdown as usual
    - ``ins``   — render its markdown, inside an ``<ins>``
    - ``del``   — the removed line as **raw text**, inside a ``<del>``; markdown that no
      longer exists should not be dressed up as though it does
    - ``replace`` — a word-level run list over the raw block, which is where an edit
      reads best; markdown is not rendered inside it for the same reason
    """
    old_lines = old.split("\n")
    new_lines = new.split("\n")
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    out: list[dict[str, t.Any]] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            out += [{"op": "equal", "line": ln} for ln in new_lines[j1:j2]]
        elif op == "delete":
            out += [{"op": "del", "line": ln} for ln in old_lines[i1:i2]]
        elif op == "insert":
            out += [{"op": "ins", "line": ln} for ln in new_lines[j1:j2]]
        else:
            out.append(
                {
                    "op": "replace",
                    "runs": _word_runs(
                        "\n".join(old_lines[i1:i2]), "\n".join(new_lines[j1:j2])
                    ),
                }
            )
    return out


def _accessory_renders(node: t.Any) -> bool:
    """Whether an accessory would draw at all.

    Mirrors what ``cv2_render.js``'s ``accessory`` refuses, because a diff must not mark
    as "removed" something the renderer was never going to draw in the first place.
    """
    if not isinstance(node, dict):
        return False
    if node.get("type") == _THUMBNAIL:
        return _media_url(node.get("media")) is not None
    if node.get("type") == _BUTTON:
        return _is_http_url(node.get("url"))
    return False


def _annotate_accessory(old: t.Any, new: t.Any) -> t.Any:
    """A section accessory's three states: gained, lost, or swapped for another."""
    new_ok = _accessory_renders(new)
    old_ok = _accessory_renders(old)
    if not new_ok:
        return [_annotate(old, "removed")] if old_ok else None
    if not old_ok:
        return [_annotate(new, "added")]
    if _node_key(old) != _node_key(new):
        return [_annotate(old, "removed"), _annotate(new, "added")]
    return new


def _annotate_pair(old: t.Any, new: t.Any) -> list[t.Any]:
    """Two same-position, differing components → the annotated nodes to draw."""
    if not (isinstance(old, dict) and isinstance(new, dict)):
        return [_annotate(new, "added")]
    to, tn = old.get("type"), new.get("type")
    if (
        to != tn
    ):  # replaced by a different kind: show the old going and the new arriving
        return [_annotate(old, "removed"), _annotate(new, "added")]
    if tn == _TEXT_DISPLAY:
        oc, nc = str(old.get("content", "")), str(new.get("content", ""))
        return [new] if oc == nc else [{**new, "_lines": _diff_lines(oc, nc)}]
    if tn in (_CONTAINER, _SECTION, _ACTION_ROW):
        # Recurse, so an edit is localised to the block that changed rather than
        # blowing away the whole container.
        merged = {
            **new,
            "components": _annotate_nodes(
                old.get("components") or [], new.get("components") or []
            ),
        }
        if tn == _SECTION:
            merged["accessory"] = _annotate_accessory(
                old.get("accessory"), new.get("accessory")
            )
        return [merged]
    # A changed leaf (media / thumbnail / separator / …): show old going, new arriving.
    return [_annotate(old, "removed"), _annotate(new, "added")]


def _annotate_replace(old_block: list, new_block: list) -> list[t.Any]:
    """A difflib 'replace' run: pair positionally; tail extras are pure add/remove."""
    out: list[t.Any] = []
    for k in range(max(len(old_block), len(new_block))):
        o = old_block[k] if k < len(old_block) else None
        n = new_block[k] if k < len(new_block) else None
        if o is None:
            out.append(_annotate(n, "added"))
        elif n is None:
            out.append(_annotate(o, "removed"))
        else:
            out += _annotate_pair(o, n)
    return out


def _annotate_nodes(old_nodes: list, new_nodes: list) -> list[t.Any]:
    """Align two sibling lists and annotate what changed."""
    a = [_node_key(n) for n in old_nodes]
    b = [_node_key(n) for n in new_nodes]
    out: list[t.Any] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(
        a=a, b=b, autojunk=False
    ).get_opcodes():
        if op == "equal":
            out += list(new_nodes[j1:j2])
        elif op == "delete":
            out += [_annotate(n, "removed") for n in old_nodes[i1:i2]]
        elif op == "insert":
            out += [_annotate(n, "added") for n in new_nodes[j1:j2]]
        else:
            out += _annotate_replace(old_nodes[i1:i2], new_nodes[j1:j2])
    return out


def _annotate_embeds(old_embeds: list, new_embeds: list) -> list[t.Any]:
    """Classic embeds, at whole-embed granularity — those edits are rare enough."""
    a = [_node_key(e) for e in old_embeds]
    b = [_node_key(e) for e in new_embeds]
    out: list[t.Any] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(
        a=a, b=b, autojunk=False
    ).get_opcodes():
        if op == "equal":
            out += list(new_embeds[j1:j2])
        else:
            out += [_annotate(e, "removed") for e in old_embeds[i1:i2]]
            out += [_annotate(e, "added") for e in new_embeds[j1:j2]]
    return out


def diff_payload(
    new_payload: dict[str, t.Any] | None,
    new_kind: str,
    old_payload: dict[str, t.Any] | None,
    old_kind: str,
) -> dict[str, t.Any]:
    """The ``new`` version annotated with what changed against ``old``.

    Three shapes, one per way this can end:

    - ``{"mode": "placeholder", "message": …}`` — a side was stored truncated, so there
      is nothing to align.
    - ``{"mode": "snapshot", "note": …, "payload": …, "kind": …}`` — the message changed
      format between versions, so the two are not comparable; show the current one.
    - ``{"mode": "diff", "note": …|None, "kind": …, …}`` — the annotated tree. ``cv2``
      carries ``components``; ``classic`` carries ``content`` (a text leaf, possibly
      with ``_lines``) and ``embeds``.
    """
    if (isinstance(new_payload, dict) and new_payload.get("truncated")) or (
        isinstance(old_payload, dict) and old_payload.get("truncated")
    ):
        return {
            "mode": "placeholder",
            "message": "Cannot diff — a version's snapshot was stored truncated.",
        }
    new_payload = new_payload or {}
    old_payload = old_payload or {}
    if new_kind != old_kind:
        return {
            "mode": "snapshot",
            "note": (
                "Message format changed since the previous version — showing the "
                "current version."
            ),
            "payload": new_payload,
            "kind": new_kind,
        }

    if new_kind == "cv2":
        old_nodes = old_payload.get("components") or []
        new_nodes = new_payload.get("components") or []
        unchanged = _node_key(old_nodes) == _node_key(new_nodes)
        body: dict[str, t.Any] = {"components": _annotate_nodes(old_nodes, new_nodes)}
    else:
        unchanged = _node_key(old_payload) == _node_key(new_payload)
        oc = str(old_payload.get("content") or "")
        nc = str(new_payload.get("content") or "")
        content: dict[str, t.Any] | None = None
        if nc.strip() or oc.strip():
            content = (
                {"type": _TEXT_DISPLAY, "content": nc}
                if oc == nc
                else {
                    "type": _TEXT_DISPLAY,
                    "content": nc,
                    "_lines": _diff_lines(oc, nc),
                }
            )
        old_embeds = [
            e for e in (old_payload.get("embeds") or []) if isinstance(e, dict)
        ]
        new_embeds = [
            e for e in (new_payload.get("embeds") or []) if isinstance(e, dict)
        ]
        body = {
            "content": content,
            "embeds": _annotate_embeds(old_embeds, new_embeds),
        }

    return {
        "mode": "diff",
        "kind": new_kind,
        "note": "No changes from the previous version." if unchanged else None,
        **body,
    }
