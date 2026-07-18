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

"""Preview wall — a read-only "scrolling wall of upcoming posts" web page.

One owner-only page (linked from the control panel via :func:`web.register_card`) that
renders, for a chosen rotation, the next several periods' posts EXACTLY as Discord shows
them — a forward-looking preview feed, scrollable into the future. No editing, no
create/edit/publish; a tab bar switches which rotation's wall is shown (``?r=<id>``).

Only rotations whose future posts are DETERMINISTICALLY computable appear:

- **lost_sector** — a daily DB rotation; the next N days come from the rotation table
  (``rotation(date)``), no Bungie API.
- **legacy_activities** (Neomuna, Moon, Dares, Rahool, …) — daily/weekly DB rotations;
  the forward window comes from :func:`dd.common.legacy_activities.iter_wall_posts`.

Xûr (fetched live from Bungie, so future weekends are unknowable) and weekly_reset /
trials (single hand-authored drafts, no forward schedule) are intentionally excluded;
they keep their own live post / web-form preview.

Each period's body markdown is rendered to safe HTML through the ONE shared render path,
:func:`dd.anchor.hybrid_post_core.render_post_spec` (the same one the web forms use).
Auth is enforced centrally by the Discord-OAuth ``web_auth`` middleware, so this module
carries no auth code.
"""

import datetime as dt
import html
import logging
from pathlib import Path

import aiohttp.web
import hikari as h
import lightbulb as lb

from ...common import cfg, legacy_activities, lost_sector, schemas
from ...common.bot import CachedFetchBot
from ...common.rotation_schema import LEGACY_DESTINATIONS
from .. import hybrid_post_core, web

logger = logging.getLogger(__name__)

loader = lb.Loader()

_PAGE_HTML_PATH = (
    Path(__file__).resolve().parent.parent / "web_static" / "preview_wall.html"
)
_TABS_PLACEHOLDER = "<!--__TABS__-->"
_POSTS_PLACEHOLDER = "<!--__POSTS__-->"
_TITLE_PLACEHOLDER = "<!--__TITLE__-->"

# How many daily Lost Sector posts to show (two weeks).
_LOST_SECTOR_DAYS = 14

#: The live bot, stashed by the StartedEvent listener so the route can fetch the guild
#: emoji (``preview_emoji_dict`` degrades to escaped ``:name:`` text when this is None).
_bot: CachedFetchBot | None = None


@loader.listener(h.StartedEvent)
async def _stash_bot(
    event: h.StartedEvent, bot: CachedFetchBot = lb.di.INJECTED
) -> None:
    global _bot
    _bot = bot


def _rotation_registry() -> list[tuple[str, str]]:
    """``(rotation_id, label)`` for every wall the page can show, in display order.

    ``lost_sector`` first, then each legacy destination as ``legacy:<key>``.
    """
    rots = [("lost_sector", "Lost Sector")]
    rots += [
        (f"legacy:{key}", title) for key, (title, _acts) in LEGACY_DESTINATIONS.items()
    ]
    return rots


async def _lost_sector_specs() -> list[tuple[str, hybrid_post_core.PostSpec]]:
    """The next ``_LOST_SECTOR_DAYS`` daily Lost Sector posts as ``(label, spec)``."""
    rotation = await lost_sector.load_rotation(buffer=5)
    details = bool(await schemas.AutoPostSettings.get_lost_sector_details_enabled())
    now = dt.datetime.now(tz=dt.UTC)
    # Align to the daily 17:00 UTC reset so entry 0 is the currently-live post.
    base = now.replace(hour=17, minute=0, second=0, microsecond=0)
    if now < base:
        base -= dt.timedelta(days=1)
    out: list[tuple[str, hybrid_post_core.PostSpec]] = []
    for i in range(_LOST_SECTOR_DAYS):
        date = base + dt.timedelta(days=i)
        body = lost_sector.build_body(rotation(date), details)
        label = date.strftime("%a, %b %d") + (" · now" if i == 0 else "")
        out.append(
            (label, hybrid_post_core.PostSpec.cv2(body, cfg.lost_sector_gif_url))
        )
    return out


async def _legacy_specs(key: str) -> list[tuple[str, hybrid_post_core.PostSpec]]:
    """A legacy destination's forward window as ``(label, PostSpec)`` (no image)."""
    rotation = await legacy_activities.load_rotation(key)
    now = dt.datetime.now(tz=dt.UTC)
    return [
        (label, hybrid_post_core.PostSpec.cv2(body, None))
        for label, body in legacy_activities.iter_wall_posts(key, rotation, now)
    ]


async def _wall_specs(rotation_id: str) -> list[tuple[str, hybrid_post_core.PostSpec]]:
    """The ``(label, PostSpec)`` list for a rotation id, or raise for an unknown id."""
    if rotation_id == "lost_sector":
        return await _lost_sector_specs()
    if rotation_id.startswith("legacy:"):
        key = rotation_id[len("legacy:") :]
        if key in LEGACY_DESTINATIONS:
            return await _legacy_specs(key)
    raise KeyError(rotation_id)


def _render_tabs(active_id: str) -> str:
    tabs = []
    for rot_id, label in _rotation_registry():
        cls = "wall-tab active" if rot_id == active_id else "wall-tab"
        href = f"/preview?r={html.escape(rot_id, quote=True)}"
        tabs.append(f'<a class="{cls}" href="{href}">{html.escape(label)}</a>')
    return "".join(tabs)


async def _render_page(rotation_id: str) -> str:
    """Render the wall page for ``rotation_id`` (tabs + the period post cards)."""
    label = dict(_rotation_registry())[rotation_id]
    emoji_dict = await hybrid_post_core.preview_emoji_dict(_bot)
    try:
        specs = await _wall_specs(rotation_id)
    except Exception:
        logger.warning("preview wall: %s build failed", rotation_id, exc_info=True)
        specs = []

    cards: list[str] = []
    for period_label, spec in specs:
        rendered = hybrid_post_core.render_post_spec(spec, emoji_dict)
        cards.append(
            f'<article class="wall-post">'
            f'<h2 class="wall-label">{html.escape(period_label)}</h2>'
            f'<div class="post-preview">{rendered}</div>'
            f"</article>"
        )
    posts = "".join(cards) or (
        '<p class="wall-empty">No upcoming posts to show — the rotation data may '
        "not be set up yet.</p>"
    )

    return (
        _PAGE_HTML_PATH.read_text(encoding="utf-8")
        .replace(_TITLE_PLACEHOLDER, html.escape(label))
        .replace(_TABS_PLACEHOLDER, _render_tabs(rotation_id))
        .replace(_POSTS_PLACEHOLDER, posts)
    )


async def _handle_get(request: aiohttp.web.Request) -> aiohttp.web.Response:
    # Auth is enforced by the web_auth middleware; this just renders the page. An
    # unknown ?r= falls back to the first rotation so a stale bookmark never 404s.
    rotation_id = request.query.get("r", "lost_sector")
    if rotation_id not in dict(_rotation_registry()):
        rotation_id = "lost_sector"
    return aiohttp.web.Response(
        text=await _render_page(rotation_id), content_type="text/html"
    )


def register_preview_wall_routes(app: aiohttp.web.Application) -> None:
    """Add the preview-wall route to the shared persistent app."""
    app.router.add_get("/preview", _handle_get)


web.register_routes(register_preview_wall_routes)
web.register_card(
    web.Card(
        "Upcoming posts",
        "Preview the next rotations' posts (Lost Sector, legacy activities)",
        "/preview",
    )
)
