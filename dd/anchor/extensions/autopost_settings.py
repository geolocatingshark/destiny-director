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

"""Autopost settings page for the anchor web control panel.

A single owner-only page (linked from the control-panel homepage via
:func:`web.register_card`) that shows every **global** autopost produce toggle and lets
the owner flip them in one place. Each toggle maps to one ``name`` row in
:class:`~dd.common.schemas.AutoPostSettings` — the same rows the scattered ``/<feed>
auto`` anchor slash commands (and ``POST /weekly_reset/auto``) write. This page does not
replace those; it is an additional, consolidated surface over the same rows.

Scope is settings only — no "send now" / preview and no per-guild follow management
(that is end-user ``/autopost <feed>`` territory, stored as ``MirroredChannel`` rows). A
missing row reads as ``None``, which every producer treats as *off*, so the page renders
``bool(get_enabled(slug))`` and lets ``set_enabled`` upsert on save. Authentication is
handled centrally by the Discord-OAuth middleware in ``web_auth.py`` (it protects every
non-allowlisted route, so this module needs no auth code).
"""

import html
import logging
import typing as t
from pathlib import Path

import aiohttp.web
import lightbulb as lb

from ...common import schemas
from .. import web

logger = logging.getLogger(__name__)

# No commands or listeners live here, but load_extensions_strict → load_extensions
# requires every extension module to expose a Loader, so define an (empty) one.
loader = lb.Loader()

_PAGE_HTML_PATH = (
    Path(__file__).resolve().parent.parent / "web_static" / "autopost_settings.html"
)
_TOGGLES_PLACEHOLDER = "<!--__TOGGLES__-->"


class _Setting(t.NamedTuple):
    """One global autopost setting.

    ``slug`` is the ``AutoPostSettings.name`` primary key; ``label`` is display copy;
    ``desc`` is a one-line explanation shown under the label; ``sub`` marks a setting
    that refines its predecessor (rendered indented) — e.g. ``lost_sector_details``
    under ``lost_sector``. ``kind`` is ``"toggle"`` (a boolean switch backed by the
    ``enabled`` column) or ``"url"`` (a text input backed by the ``value`` column, e.g.
    ``eververse_image_url``).
    """

    slug: str
    label: str
    desc: str
    sub: bool
    kind: str = "toggle"


# Ordered for display: each sub-toggle immediately follows its parent. Every slug here
# is an AutoPostSettings row a producer checks before posting (see dd/anchor/extensions/
# lost_sector.py, xur.py, etc.).
_SETTINGS: tuple[_Setting, ...] = (
    _Setting(
        "lost_sector",
        "Lost Sector",
        "Today's Lost Sector — location, champions, and shields.",
        False,
    ),
    _Setting(
        "lost_sector_details",
        "Legendary weapon details",
        "Also list the featured legendary weapon rewards.",
        True,
    ),
    _Setting("xur", "Xûr", "Xûr's weekend location and inventory.", False),
    _Setting(
        "xur_default_image",
        "Use default image",
        "Fall back to a saved banner when no fresh image is available.",
        True,
    ),
    _Setting(
        "eververse",
        "Eververse",
        "This week's Eververse featured items and Bright Dust.",
        False,
    ),
    _Setting(
        "eververse_image_url",
        "Default image URL",
        "Banner shown at the bottom of each Eververse post. Leave blank for none.",
        True,
        "url",
    ),
    _Setting("ada", "Ada-1", "Ada-1's weekly rotating shaders.", False),
    _Setting(
        "portal_ops",
        "Portal Ops",
        "Today's featured Portal Ops and their guaranteed rewards.",
        False,
    ),
    _Setting(
        "weekly_reset",
        "Weekly Reset",
        "Tuesday reset overview — activities, rotators, and rewards.",
        False,
    ),
    _Setting(
        "iron_banner",
        "Iron Banner",
        "Iron Banner weeks — dates, game modes, bonus focus pool, and guide link.",
        False,
    ),
)

# The slugs this page is allowed to write — a save request's keys are filtered against
# this so an unknown/forged key can never create a stray AutoPostSettings row. Split by
# kind so a save routes each to the right column (``enabled`` vs ``value``).
_TOGGLE_SLUGS = frozenset(s.slug for s in _SETTINGS if s.kind == "toggle")
_URL_SLUGS = frozenset(s.slug for s in _SETTINGS if s.kind == "url")


def _render_row(setting: _Setting, state: bool | str | None) -> str:
    """Render one settings row: label + description, then its control.

    A ``toggle`` setting renders a checkbox styled by CSS (see autopost_settings.html)
    as an iOS-style switch, keyed by ``data-slug``. A ``url`` setting renders a
    full-width text input (below its label) keyed by the same ``data-slug`` — both are
    what the client save script and ``_handle_save`` read back.
    """
    base_class = "row sub" if setting.sub else "row"
    label_block = (
        '<div class="text">'
        f'<div class="name">{html.escape(setting.label)}</div>'
        f'<div class="desc">{html.escape(setting.desc)}</div>'
        "</div>"
    )
    if setting.kind == "url":
        value = html.escape(state or "") if isinstance(state, str) else ""
        return (
            f'<div class="{base_class} urlrow">'
            f"{label_block}"
            '<input type="url" class="urlfield" '
            f'data-slug="{html.escape(setting.slug)}"'
            f' value="{value}" placeholder="https://example.com/banner.png" />'
            "</div>"
        )
    checked = " checked" if state else ""
    return (
        f'<div class="{base_class}">'
        f"{label_block}"
        '<label class="switch">'
        f'<input type="checkbox" data-slug="{html.escape(setting.slug)}"{checked} />'
        '<span class="slider"></span>'
        "</label>"
        "</div>"
    )


async def _render_html() -> str:
    """Render the settings page with the current DB state substituted in.

    A top-level setting (``sub`` is False) and every sub-setting that follows it share
    one ``.group`` box, so a feed and its content sub-toggles read as one category. A
    parent always precedes its subs in ``_SETTINGS``, so a single pass groups them.
    """
    groups: list[str] = []
    current: list[str] = []
    async with schemas.db_session() as session:
        for setting in _SETTINGS:
            if setting.kind == "url":
                state: bool | str | None = await schemas.AutoPostSettings.get_value(
                    setting.slug, session=session
                )
            else:
                state = bool(
                    await schemas.AutoPostSettings.get_enabled(
                        setting.slug, session=session
                    )
                )
            row = _render_row(setting, state)
            if setting.sub:
                current.append(row)
            else:
                if current:
                    groups.append(f'<div class="group">{"".join(current)}</div>')
                current = [row]
        if current:
            groups.append(f'<div class="group">{"".join(current)}</div>')
    return _PAGE_HTML_PATH.read_text(encoding="utf-8").replace(
        _TOGGLES_PLACEHOLDER, "".join(groups)
    )


async def _handle_get(request: aiohttp.web.Request) -> aiohttp.web.Response:
    # Auth is enforced by the web_auth middleware; this just renders the page.
    return aiohttp.web.Response(text=await _render_html(), content_type="text/html")


async def _handle_save(request: aiohttp.web.Request) -> aiohttp.web.Response:
    # The middleware already enforced auth + Origin (CSRF); mirror weekly_reset's save.
    try:
        payload = await request.json()
    except Exception:
        return aiohttp.web.json_response({"error": "Malformed body."}, status=400)

    settings = payload.get("settings")
    if not isinstance(settings, dict):
        return aiohttp.web.json_response(
            {"error": "Expected a 'settings' object."}, status=400
        )

    # Validate URL fields up front so a bad value fails the whole save (before opening
    # a transaction) rather than persisting a URL Discord can't fetch. A blank field
    # clears the setting (stored as NULL → "no image").
    url_values: dict[str, str | None] = {}
    for slug in _URL_SLUGS:
        if slug not in settings:
            continue
        raw = settings[slug]
        if not isinstance(raw, str):
            return aiohttp.web.json_response(
                {"error": f"'{slug}' must be a string."}, status=400
            )
        trimmed = raw.strip()
        if trimmed and not trimmed.startswith(("http://", "https://")):
            return aiohttp.web.json_response(
                {"error": f"'{slug}' must be an http(s) URL."}, status=400
            )
        url_values[slug] = trimmed or None

    # Only known slugs are honoured; unknown keys are ignored (never trust the client's
    # key set to spawn rows). One transaction so a batch save is all-or-nothing.
    async with schemas.db_session() as session, session.begin():
        for slug, value in settings.items():
            if slug in _TOGGLE_SLUGS:
                await schemas.AutoPostSettings.set_enabled(
                    slug, bool(value), session=session
                )
        for slug, url in url_values.items():
            await schemas.AutoPostSettings.set_value(slug, url, session=session)

    return aiohttp.web.json_response({"ok": True})


def register_autopost_settings_routes(app: aiohttp.web.Application) -> None:
    """Add the autopost-settings routes to the shared persistent app."""
    app.router.add_get("/autopost_settings", _handle_get)
    app.router.add_post("/autopost_settings/save", _handle_save)


web.register_routes(register_autopost_settings_routes)
web.register_card(
    web.Card(
        "Autopost Settings",
        "Toggle which feeds anchor posts",
        "/autopost_settings",
    )
)
