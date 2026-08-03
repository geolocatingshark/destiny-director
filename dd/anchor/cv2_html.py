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

"""The web Components-V2 builder's server-side render — the authoritative preview.

The builder authors a raw CV2 node list (:mod:`cv2_nodes`) client-side and POSTs it here
to render safe HTML. That render is **not** new code — it composes three pieces that
already exist, so the preview always tracks the real post:

- :func:`cv2_nodes.sanitize_for_preview` downgrades a mid-construction tree (an empty
  container, a section still missing its accessory) to friendly placeholder text, so a
  half-built message renders cleanly rather than erroring — the very sanitize the
  in-Discord ``/post components`` builder previews through.
- :func:`hybrid_post_core._html_emoji_substituter` resolves the ``:name:`` shortcodes a
  web author types against the live guild emoji dict, exactly like the classic post
  previewer. (:mod:`cv2_render`'s own default substituter resolves *captured*
  ``<:name:id>`` emoji straight off the CDN — the shape a mirror snapshot carries, never
  what a web author types.)
- :func:`cv2_render.render_snapshot` is the CV2 tree → safe-HTML walker (whitelisted
  ``{span, strong, em, a, img}`` tags, escaped leaves, ``http(s)``-validated URLs)
  already shipped for the mirror-log render pane.

**Why the client also renders.** The builder's canvas *is* the preview — you click a
block to select it and type straight into the text — so a server round-trip per
keystroke is not an option; ``cv2_model.js`` renders the canvas locally. That is not an
XSS weakening: the anchor web UI is gated by Discord OAuth to bot owners only
(:mod:`extensions.web_auth`), so a client-side render shows an owner their *own*
markdown in their *own* browser. The untrusted-content sink is the mirror log, which
renders other people's posts and stays server-rendered.

This module is what makes that safe to rely on: it is the render the author confirms
against before publishing, and :func:`cv2_nodes.validate` plus this sanitize run
server-side on publish regardless of what the client believed.
"""

import hikari as h

from . import cv2_nodes, cv2_render
from .hybrid_post_core import _html_emoji_substituter


def render_cv2_nodes_html(
    nodes: list[cv2_nodes.Node], emoji_dict: dict[str, h.Emoji]
) -> str:
    """Render an authored CV2 node list to safe preview HTML.

    ``nodes`` is the raw node list the builder emits, possibly still mid-construction.
    The result is trusted, pre-escaped HTML for the page's ``innerHTML`` sink — every
    text leaf escaped, every URL ``http(s)``-validated, only the shared tag whitelist.
    """
    safe = cv2_nodes.sanitize_for_preview(nodes)
    emoji_sub = _html_emoji_substituter(emoji_dict)
    return cv2_render.render_snapshot({"components": safe}, "cv2", emoji_sub=emoji_sub)
