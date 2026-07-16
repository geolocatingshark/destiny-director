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

"""Web editor for the rotation JSON store (anchor).

``/rotation edit`` links the owner to ``{public_base_url}/rotation``, a homepage listing
every rotation type (``ROTATION_SCHEMAS``); each links to ``/rotation/edit?type=…`` so
the owner edits the document with a friendly form, previews the rendered post, and saves
— the server re-validates against the JSON schema on save. Authentication for every
page, preview and save is handled centrally by the Discord-OAuth middleware in
``web_auth.py`` (this module carries no auth code of its own).
"""

import asyncio
import datetime as dt
import html
import json
import logging
import typing as t
from pathlib import Path

import aiohttp.web
import hikari as h
import lightbulb as lb

from ...common import cfg, rotation_schema, schemas
from ...common.components import cv2_error, cv2_notice, respond_cv2
from ...common.legacy_activities import load_seed_doc, weapon_values
from ...sector_accounting import (
    legacy_activities,
    sector_accounting,
    xur as xur_support_data,
)
from .. import web
from .bungie_api import item_index

logger = logging.getLogger(__name__)

loader = lb.Loader()

_EDITOR_HTML_PATH = (
    Path(__file__).resolve().parent.parent / "web_static" / "editor.html"
)
_HOME_HTML_PATH = (
    Path(__file__).resolve().parent.parent / "web_static" / "rotation_home.html"
)
# Days of rendered output the preview spans (covers a daily reset).
_PREVIEW_DAYS = 4


# --- document helpers -------------------------------------------------------------


def _default_doc(post_type: str) -> dict[str, t.Any]:
    """An empty-but-renderable scaffold for a post type with no stored data yet."""
    if post_type == "lost_sector":
        return {
            "version": 1,
            "reference_date": "",
            "schedule": {zone: [] for zone in rotation_schema.LOST_SECTOR_ZONES},
            "sectors": [],
        }
    if post_type == "xur_location":
        return {"version": 1, "locations": []}
    if post_type == rotation_schema.TRIALS_LOOT_SLUG:
        # Start populated with the baked default sets + a one-loop schedule (so the
        # editor isn't blank), matching the producer's runtime fallback.
        return rotation_schema.trials_loot_default_doc()
    if rotation_schema.is_world_activity(post_type):
        return rotation_schema.legacy_default_doc(post_type)
    return {}


def _vocab() -> dict[str, t.Any]:
    return {
        "champions": rotation_schema.CHAMPION_TYPES,
        "shields": rotation_schema.SHIELD_ELEMENTS,
        "zones": rotation_schema.LOST_SECTOR_ZONES,
    }


def _render_preview_html(rotation: sector_accounting.Rotation) -> str:
    """A compact HTML rendering of the next few days of the rotation."""
    today = dt.datetime.now(dt.UTC)
    blocks: list[str] = []
    for offset in range(_PREVIEW_DAYS):
        date = today + dt.timedelta(days=offset)
        blocks.append(f"<h4>{date.date().isoformat()}</h4>")
        try:
            sectors = rotation(date)
        except KeyError:
            blocks.append("<p><em>No data (TBC) for this day.</em></p>")
            continue
        items = []
        for sector in sectors:
            champions = (
                ", ".join(
                    sorted(
                        set(
                            sector.expert_data.champions_list
                            + sector.master_data.champions_list
                        )
                    )
                )
                or "None"
            )
            shields = (
                ", ".join(
                    sorted(
                        set(
                            sector.expert_data.shields_list
                            + sector.master_data.shields_list
                        )
                    )
                )
                or "None"
            )
            name = html.escape(sector.name)
            link = html.escape(sector.shortlink_gfx, quote=True)
            items.append(
                f"<li><a href='{link}' target='_blank' rel='noopener'>{name}</a>"
                f" — champions: {champions}; shields: {shields}</li>"
            )
        blocks.append("<ul>" + "".join(items) + "</ul>")
    return "".join(blocks)


def _render_xur_location_preview_html(
    locations: xur_support_data.XurLocations,
) -> str:
    """A compact HTML rendering of the resolved Xûr location map."""
    items: list[str] = []
    for loc in locations.values():
        friendly = html.escape(loc.friendly_location_name or loc.api_location_name)
        api_name = html.escape(loc.api_location_name)
        if loc.link:
            link = html.escape(loc.link, quote=True)
            label = f"<a href='{link}' target='_blank' rel='noopener'>{friendly}</a>"
        else:
            label = friendly
        items.append(f"<li>{label} <small>(API: {api_name})</small></li>")
    if not items:
        return "<p><em>No locations defined yet.</em></p>"
    return "<ul>" + "".join(items) + "</ul>"


def _render_legacy_preview_html(
    rotation: legacy_activities.LegacyRotation,
) -> str:
    """A compact HTML rendering of the next few periods of a legacy destination."""
    today = dt.datetime.now(dt.UTC)
    step = rotation.step
    blocks: list[str] = []
    for offset in range(_PREVIEW_DAYS):
        date = today + step * offset
        blocks.append(f"<h4>{date.date().isoformat()}</h4>")
        items: list[str] = []
        for activity in rotation(date):
            title = html.escape(activity.title)
            if activity.set is not None:
                s = activity.set
                weapons = ", ".join(s.weapons) or "—"
                armor = ", ".join(s.armor)
                detail = f"{html.escape(s.name)} — weapons: {html.escape(weapons)}" + (
                    f"; armor: {html.escape(armor)} (all classes)" if armor else ""
                )
                items.append(f"<li>{title} — {detail}</li>")
                continue
            parts: list[str] = []
            for name, value in activity.values.items():
                if not value:
                    continue
                parts.append(f"{html.escape(name)}: {html.escape(value)}")
            detail = "; ".join(parts) or "<em>TBC</em>"
            items.append(f"<li>{title} — {detail}</li>")
        blocks.append("<ul>" + "".join(items) + "</ul>")
    return "".join(blocks)


# --- per-type dispatch ------------------------------------------------------------


def _build_trials_loot(data: dict[str, t.Any]) -> list[tuple[str, list[str]]]:
    """Expand the loot doc into its looping ``(set_name, weapons)`` schedule.

    The hard gate beyond the schema: every schedule entry must name a defined set
    (otherwise the producer would silently drop that week). Returns the expanded
    rotation, which the preview renders."""
    sets = {
        str(s["name"]): list(s.get("weapons") or []) for s in data.get("sets") or []
    }
    schedule = [str(n) for n in data.get("schedule") or []]
    missing = sorted({n for n in schedule if n not in sets})
    if missing:
        raise ValueError("schedule references undefined set(s): " + ", ".join(missing))
    return [(n, sets[n]) for n in schedule]


def _render_trials_loot_preview_html(rotation: list[tuple[str, list[str]]]) -> str:
    """The looping loot schedule, one week per row (the order the producer walks)."""
    if not rotation:
        return "<p><em>No schedule defined yet.</em></p>"
    items: list[str] = []
    for name, weapons in rotation:
        listed = ", ".join(html.escape(w) for w in weapons if w) or "—"
        items.append(f"<li><strong>{html.escape(name)}</strong> — {listed}</li>")
    return "<ol>" + "".join(items) + "</ol>"


def _build_domain_object(post_type: str, data: t.Any) -> t.Any:
    """Construct the domain object for ``post_type`` (a hard gate beyond the schema).

    Raises if the document is structurally unusable — caught by the preview / save
    handlers and surfaced to the editor. New rotation types register their builder
    here alongside :data:`rotation_schema.ROTATION_SCHEMAS`.
    """
    if post_type == "xur_location":
        return xur_support_data.XurLocations.from_json(data)
    if post_type == rotation_schema.TRIALS_LOOT_SLUG:
        return _build_trials_loot(data)
    if rotation_schema.is_world_activity(post_type):
        return legacy_activities.LegacyRotation.from_json(data)
    return sector_accounting.Rotation.from_json(data)


def _render_preview(post_type: str, obj: t.Any) -> str:
    if post_type == "xur_location":
        return _render_xur_location_preview_html(obj)
    if post_type == rotation_schema.TRIALS_LOOT_SLUG:
        return _render_trials_loot_preview_html(obj)
    if rotation_schema.is_world_activity(post_type):
        return _render_legacy_preview_html(obj)
    return _render_preview_html(obj)


# --- route handlers ---------------------------------------------------------------


def _read_json_body(payload: t.Any) -> tuple[str, t.Any]:
    """Pull ``(type, data)`` from a parsed JSON POST body (auth is via the cookie)."""
    post_type = str(payload.get("type", ""))
    data = payload.get("data")
    return post_type, data


def _render_home_html() -> str:
    """The rotations homepage: one link per ``ROTATION_SCHEMAS`` type."""
    items: list[str] = []
    for slug in sorted(rotation_schema.ROTATION_SCHEMAS):
        title = html.escape(
            str(rotation_schema.ROTATION_SCHEMAS[slug].get("title", slug))
        )
        slug_attr = html.escape(slug, quote=True)
        items.append(
            f'<li><a href="/rotation/edit?type={slug_attr}">{title}</a>'
            f" <code>{html.escape(slug)}</code></li>"
        )
    list_html = '<ul class="rotations">' + "".join(items) + "</ul>"
    return _HOME_HTML_PATH.read_text(encoding="utf-8").replace(
        "<!--__ROTATIONS__-->", list_html
    )


async def _handle_home_get(request: aiohttp.web.Request) -> aiohttp.web.Response:
    # Auth is enforced by the web_auth middleware; this just renders the homepage.
    return aiohttp.web.Response(text=_render_home_html(), content_type="text/html")


async def _handle_edit_get(request: aiohttp.web.Request) -> aiohttp.web.Response:
    post_type = request.query.get("type", "")
    if post_type not in rotation_schema.ROTATION_SCHEMAS:
        return aiohttp.web.Response(
            status=404, text=f"Unknown rotation type {post_type!r}."
        )
    # Re-bind to the canonical allowlist key so the value reflected into the page below
    # is sourced from our constant schema table, not the raw query string (clears
    # CodeQL's reflected-XSS taint; the bootstrap JSON is additionally `<`-escaped for
    # its inline <script> context).
    post_type = next(k for k in rotation_schema.ROTATION_SCHEMAS if k == post_type)

    doc = await schemas.RotationData.get_data(post_type)
    if doc is None:
        doc = _default_doc(post_type)

    bootstrap = {"type": post_type, "data": doc, "vocab": _vocab()}
    # Escape "<" so a "</script>" in the data can't break out of the inline <script>.
    bootstrap_js = json.dumps(bootstrap).replace("<", "\\u003c")
    page = _EDITOR_HTML_PATH.read_text(encoding="utf-8").replace(
        "/*__BOOTSTRAP__*/ null", bootstrap_js
    )
    return aiohttp.web.Response(text=page, content_type="text/html")


async def _handle_preview(request: aiohttp.web.Request) -> aiohttp.web.Response:
    try:
        payload = await request.json()
    except Exception:
        return aiohttp.web.Response(status=400, text="Malformed request body.")
    post_type, data = _read_json_body(payload)
    if post_type not in rotation_schema.ROTATION_SCHEMAS:
        return aiohttp.web.Response(
            status=400, text=f"Unknown rotation type {post_type!r}."
        )

    try:
        rotation_schema.validate(post_type, data)
    except Exception as e:
        return aiohttp.web.Response(status=400, text=f"Document is invalid:\n{e}")

    try:
        obj = _build_domain_object(post_type, data)
        body = _render_preview(post_type, obj)
    except Exception as e:
        return aiohttp.web.Response(status=400, text=f"Could not render preview:\n{e}")
    return aiohttp.web.Response(text=body, content_type="text/html")


async def _handle_edit_post(request: aiohttp.web.Request) -> aiohttp.web.Response:
    try:
        payload = await request.json()
    except Exception:
        return aiohttp.web.Response(status=400, text="Malformed request body.")
    post_type, data = _read_json_body(payload)
    if post_type not in rotation_schema.ROTATION_SCHEMAS:
        return aiohttp.web.Response(
            status=400, text=f"Unknown rotation type {post_type!r}."
        )

    try:
        rotation_schema.validate(post_type, data)
    except Exception as e:
        return aiohttp.web.Response(status=400, text=f"Document is invalid:\n{e}")

    # Hard gate: the document must build into its domain object (catches structural
    # issues the schema alone doesn't, e.g. a bad reference_date that parses but not as
    # a date).
    try:
        _build_domain_object(post_type, data)
    except Exception as e:
        return aiohttp.web.Response(status=400, text=f"Document is unusable:\n{e}")

    if rotation_schema.is_world_activity(post_type):
        _bake_item_links(data)

    await schemas.RotationData.set_data(post_type, data)
    logger.info("Rotation data for %s saved via web editor", post_type)
    return aiohttp.web.Response(text="Saved")


def _bake_item_links(data: dict[str, t.Any]) -> None:
    """Resolve a legacy doc's weapon values to light.gg URLs, in place (``item_links``).

    Server-owned: recomputed on every save. If the manifest index isn't warm yet the doc
    just saves without links — they appear on a later save (or the backfill script)."""
    data.pop("item_links", None)
    if not item_index.ready():
        return
    links: dict[str, str] = {}
    for value in weapon_values(data):
        url = item_index.resolve_light_gg_url(value)
        if url:
            links[value] = url
    if links:
        data["item_links"] = links


async def _handle_reset(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """Reset a world-activity rotation to its committed seed document ("defaults").

    The recovery path for a stored doc that has gone bad (e.g. an unparseable row a
    command surfaces as an error): the operator resets it here to the known-good seed.
    Only world-activity types ship a seed; the seed already carries its own baked
    ``item_links`` so it's saved verbatim (no manifest access needed)."""
    try:
        payload = await request.json()
    except Exception:
        return aiohttp.web.Response(status=400, text="Malformed request body.")
    post_type = str(payload.get("type", ""))
    if not rotation_schema.is_world_activity(post_type):
        return aiohttp.web.Response(
            status=400,
            text=f"{post_type!r} has no committed defaults to reset to.",
        )

    key = post_type.removeprefix(rotation_schema.ROTATION_SLUG_PREFIX)
    doc = load_seed_doc(key)
    if doc is None:
        return aiohttp.web.Response(
            status=404, text=f"No committed seed document for {post_type!r}."
        )

    # Sanity-gate the seed (it's committed, but a broken seed must not overwrite live
    # data): it has to validate and build before we persist it.
    try:
        rotation_schema.validate(post_type, doc)
        _build_domain_object(post_type, doc)
    except Exception as e:
        return aiohttp.web.Response(
            status=500, text=f"Committed seed for {post_type!r} is itself invalid:\n{e}"
        )

    await schemas.RotationData.set_data(post_type, doc)
    logger.info(
        "Rotation data for %s reset to committed defaults via web editor", post_type
    )
    return aiohttp.web.Response(text="Reset to defaults")


async def _handle_search(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """Autocomplete for the editor: manifest weapon/armor items matching ``?q=``."""
    query = request.query.get("q", "")
    kind = request.query.get("kind") or None
    return aiohttp.web.json_response(item_index.search(query, kind=kind))


def register_rotation_routes(app: aiohttp.web.Application) -> None:
    """Add the rotation editor routes to the shared persistent app."""
    app.router.add_get("/rotation", _handle_home_get)
    app.router.add_get("/rotation/edit", _handle_edit_get)
    app.router.add_post("/rotation/edit", _handle_edit_post)
    app.router.add_post("/rotation/preview", _handle_preview)
    app.router.add_post("/rotation/reset", _handle_reset)
    app.router.add_get("/rotation/search", _handle_search)


web.register_routes(register_rotation_routes)


# Hold a strong reference to the background warm task: the event loop keeps only a weak
# ref to a bare create_task(), so without this the task can be garbage-collected —
# and cancelled — mid-download, leaving the index cold for the whole process.
_warm_tasks: set[asyncio.Task[None]] = set()


@loader.listener(h.StartedEvent)
async def _warm_item_index(_event: h.StartedEvent) -> None:
    """Build the manifest weapon/armor index in the background (for autocomplete + link
    baking), so requests never block on the (large) manifest download."""
    task = asyncio.create_task(item_index.warm(schemas.BungieCredentials.api_key))
    _warm_tasks.add(task)
    task.add_done_callback(_warm_tasks.discard)


web.register_card(
    web.Card(
        "Rotation Editor",
        "Edit rotation post data (Xûr, weekly rotations, …)",
        "/rotation",
    )
)


# --- slash commands ---------------------------------------------------------------


rotation = lb.Group("rotation", "Edit rotation post data (owner only)")


@rotation.register
class Edit(
    lb.SlashCommand,
    name="edit",
    description="Open the web editor for all rotation post data",
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context) -> None:
        if not cfg.public_base_url:
            await respond_cv2(
                ctx,
                cv2_error(
                    "No editor link available",
                    "No public base URL is configured (set PUBLIC_BASE_URL or run "
                    "on Railway), so I can't mint a reachable edit link.",
                ),
                ephemeral=True,
            )
            return

        url = f"{cfg.public_base_url}/rotation"
        await respond_cv2(
            ctx,
            cv2_notice(
                f"[Open the rotation editor here]({url}) — it lists every rotation. "
                "You'll sign in with Discord the first time."
            ),
            ephemeral=True,
        )


loader.command(rotation)
