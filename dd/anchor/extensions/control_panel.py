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

"""Web control panel for the anchor process (anchor).

A card-based landing page at ``/`` listing every web page/tool. Cards are contributed by
each feature module at import time via :func:`web.register_card` (mirroring how routes
are contributed via :func:`web.register_routes`), so a new page appears here without
editing this module. Authentication is handled centrally by the Discord-OAuth middleware
in ``web_auth.py`` — it protects every non-allowlisted route by default, so ``/`` is
gated with no extra code here. ``/control_panel`` links the owner to the panel URL.
"""

import html
import logging
from pathlib import Path

import aiohttp.web
import lightbulb as lb

from ...common import cfg
from ...common.components import cv2_error, cv2_notice, respond_cv2
from .. import web

logger = logging.getLogger(__name__)

loader = lb.Loader()

_PANEL_HTML_PATH = (
    Path(__file__).resolve().parent.parent / "web_static" / "control_panel.html"
)
_CARDS_PLACEHOLDER = "<!--__CARDS__-->"


def _render_panel_html() -> str:
    """Render the control panel, substituting the card grid for the placeholder."""
    cards = sorted(web.registered_cards())
    if cards:
        items = "".join(
            f'<a class="card" href="{html.escape(card.href)}">'
            f'<div class="title">{html.escape(card.title)}</div>'
            f'<div class="desc">{html.escape(card.description)}</div>'
            "</a>"
            for card in cards
        )
    else:
        items = '<p class="empty">No web tools are available.</p>'
    return _PANEL_HTML_PATH.read_text(encoding="utf-8").replace(
        _CARDS_PLACEHOLDER, items
    )


async def _handle_panel(request: aiohttp.web.Request) -> aiohttp.web.Response:
    return aiohttp.web.Response(text=_render_panel_html(), content_type="text/html")


def register_panel_routes(app: aiohttp.web.Application) -> None:
    """Add the control-panel route to the shared persistent app."""
    app.router.add_get("/", _handle_panel)


web.register_routes(register_panel_routes)


# --- slash commands ---------------------------------------------------------------


class ControlPanel(
    lb.SlashCommand,
    name="control_panel",
    description="Open the anchor web control panel",
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context) -> None:
        if not cfg.public_base_url:
            await respond_cv2(
                ctx,
                cv2_error(
                    "No control panel link available",
                    "No public base URL is configured (set PUBLIC_BASE_URL or run "
                    "on Railway), so I can't mint a reachable link.",
                ),
                ephemeral=True,
            )
            return

        url = f"{cfg.public_base_url}/"
        await respond_cv2(
            ctx,
            cv2_notice(
                f"[Open the control panel here]({url}) — it lists every web tool. "
                "You'll sign in with Discord the first time."
            ),
            ephemeral=True,
        )


loader.command(ControlPanel)
