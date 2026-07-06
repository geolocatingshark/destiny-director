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

"""Cookie-authenticated web editor for the rotation JSON store (anchor).

``/rotation edit`` mints a short-lived editor *session* and DMs the owner a link to
``{public_base_url}/rotation?token=…``. Opening it trades the token for a session cookie
and shows a homepage listing every rotation type (``ROTATION_SCHEMAS``); each links to
``/rotation/edit?type=…`` where the owner edits the document with a friendly form,
previews the rendered post, and saves — the server re-validates against the JSON schema
on save. The session cookie authorises every page, preview and save for its ~2-hour
life and is not burned on save (edit many rotations in one sitting).
``/rotation import_from_sheet`` does the one-shot gspread → DB import with a
rendered-parity check, for the cutover.
"""

import asyncio
import datetime as dt
import hashlib
import hmac
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
from ...sector_accounting import (
    sector_accounting,
    xur as xur_support_data,
)
from .. import web

logger = logging.getLogger(__name__)

loader = lb.Loader()

_EDITOR_HTML_PATH = (
    Path(__file__).resolve().parent.parent / "web_static" / "editor.html"
)
_HOME_HTML_PATH = (
    Path(__file__).resolve().parent.parent / "web_static" / "rotation_home.html"
)
_SESSION_TTL = dt.timedelta(hours=2)
_SESSION_COOKIE = "rotation_session"
_EXPIRED_MSG = "This link has expired. Run /rotation edit for a fresh one."
# Days of rendered output the preview / parity check spans (covers a daily reset).
_PREVIEW_DAYS = 4
_PARITY_DAYS = 18


# --- session manager --------------------------------------------------------------


def _signing_key() -> bytes:
    """A stable secret key for signing session tokens.

    Derived from the anchor bot token — a secret that persists across restarts and never
    leaves the server — so no new env var (and no Railway config) is needed. Deriving
    (rather than using it raw) keeps the bot token itself out of the signing material.
    """
    return hashlib.sha256(
        b"rotation-editor-session|" + cfg.discord_token_anchor.encode()
    ).digest()


class RotationSessionManager:
    """Stateless, signed editor-session tokens (no server-side store).

    A token is ``"<expiry_epoch>.<hex_hmac>"``, where the HMAC (SHA-256, keyed by a
    secret derived from the anchor bot token) covers the expiry. Validation recomputes
    the HMAC and checks the expiry — so a token stays valid across process restarts and
    would work across replicas, unlike the previous in-memory store. Minted by the
    owner-only ``/rotation edit`` command and carried in a cookie; it is multi-use for
    its whole ~2-hour life (homepage, every editor page, previews, saves) and there
    is nothing to revoke early — tokens simply expire, which matches the no-burn design.
    """

    @classmethod
    def _sign(cls, expiry_epoch: int) -> str:
        sig = hmac.new(
            _signing_key(), str(expiry_epoch).encode(), hashlib.sha256
        ).hexdigest()
        return f"{expiry_epoch}.{sig}"

    @classmethod
    def mint(cls) -> str:
        expiry = dt.datetime.now(dt.UTC) + _SESSION_TTL
        return cls._sign(int(expiry.timestamp()))

    @classmethod
    def resolve(cls, token: str) -> bool:
        """Whether ``token`` is a well-signed, unexpired session token."""
        expiry_str, _, _sig = token.partition(".")
        try:
            expiry_epoch = int(expiry_str)
        except ValueError:
            return False
        # Constant-time compare of the whole "<exp>.<sig>" against a fresh signature —
        # rejects both a tampered expiry and a bad signature.
        if not hmac.compare_digest(token, cls._sign(expiry_epoch)):
            return False
        return expiry_epoch > int(dt.datetime.now(dt.UTC).timestamp())


# --- cookie / origin helpers ------------------------------------------------------


def _session_from_request(request: aiohttp.web.Request) -> str:
    return request.cookies.get(_SESSION_COOKIE, "")


def _set_session_cookie(response: aiohttp.web.StreamResponse, token: str) -> None:
    response.set_cookie(
        _SESSION_COOKIE,
        token,
        max_age=int(_SESSION_TTL.total_seconds()),
        httponly=True,
        # Secure only when we're actually served over https (local http tunnels can't
        # set a Secure cookie); Railway's public_base_url is https.
        secure=cfg.public_base_url.startswith("https"),
        # Lax, NOT Strict: the owner always arrives via a cross-site top-level click
        # from Discord (and a same-origin redirect), and Strict cookies are withheld on
        # any navigation whose redirect chain was initiated cross-site — which would
        # 401 the homepage every time. Lax is sent on top-level GET navigations while
        # still being withheld on cross-site POSTs (so CSRF is still covered, alongside
        # the Origin check below).
        samesite="Lax",
        path="/rotation",
    )


def _origin_ok(request: aiohttp.web.Request) -> bool:
    """Reject cross-site POSTs (defence-in-depth atop the SameSite=Lax cookie).

    A browser sends ``Origin`` on state-changing fetches; if present it must match our
    own origin. Absent (non-browser client, or an origin-less navigation) we defer to
    SameSite. If no public origin is configured we can't compare, so we allow.
    """
    origin = request.headers.get("Origin")
    if not origin or not cfg.public_base_url:
        return True
    return origin.rstrip("/") == cfg.public_base_url.rstrip("/")


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


# --- per-type dispatch ------------------------------------------------------------


def _build_domain_object(post_type: str, data: t.Any) -> t.Any:
    """Construct the domain object for ``post_type`` (a hard gate beyond the schema).

    Raises if the document is structurally unusable — caught by the preview / save
    handlers and surfaced to the editor. New rotation types register their builder
    here alongside :data:`rotation_schema.ROTATION_SCHEMAS`.
    """
    if post_type == "xur_location":
        return xur_support_data.XurLocations.from_json(data)
    return sector_accounting.Rotation.from_json(data)


def _render_preview(post_type: str, obj: t.Any) -> str:
    if post_type == "xur_location":
        return _render_xur_location_preview_html(obj)
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
    # Entry: a fresh ?token=… from the slash command. Trade it for a session cookie and
    # redirect to the bare homepage so the token doesn't linger in the URL / history.
    entry_token = request.query.get("token", "")
    if entry_token:
        if not RotationSessionManager.resolve(entry_token):
            return aiohttp.web.Response(status=401, text=_EXPIRED_MSG)
        response = aiohttp.web.HTTPFound("/rotation")
        _set_session_cookie(response, entry_token)
        return response

    if not RotationSessionManager.resolve(_session_from_request(request)):
        return aiohttp.web.Response(status=401, text=_EXPIRED_MSG)

    return aiohttp.web.Response(text=_render_home_html(), content_type="text/html")


async def _handle_edit_get(request: aiohttp.web.Request) -> aiohttp.web.Response:
    if not RotationSessionManager.resolve(_session_from_request(request)):
        return aiohttp.web.Response(status=401, text=_EXPIRED_MSG)

    post_type = request.query.get("type", "")
    if post_type not in rotation_schema.ROTATION_SCHEMAS:
        return aiohttp.web.Response(
            status=404, text=f"Unknown rotation type {post_type!r}."
        )

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
    if not RotationSessionManager.resolve(_session_from_request(request)):
        return aiohttp.web.Response(status=401, text=_EXPIRED_MSG)
    if not _origin_ok(request):
        return aiohttp.web.Response(status=403, text="Cross-origin request refused.")
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
    if not RotationSessionManager.resolve(_session_from_request(request)):
        return aiohttp.web.Response(status=401, text=_EXPIRED_MSG)
    if not _origin_ok(request):
        return aiohttp.web.Response(status=403, text="Cross-origin request refused.")
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

    await schemas.RotationData.set_data(post_type, data)
    # Session is NOT burned: the owner keeps editing other rotations / re-saving.
    logger.info("Rotation data for %s saved via web editor", post_type)
    return aiohttp.web.Response(text="Saved")


def register_rotation_routes(app: aiohttp.web.Application) -> None:
    """Add the rotation editor routes to the shared persistent app."""
    app.router.add_get("/rotation", _handle_home_get)
    app.router.add_get("/rotation/edit", _handle_edit_get)
    app.router.add_post("/rotation/edit", _handle_edit_post)
    app.router.add_post("/rotation/preview", _handle_preview)


web.register_routes(register_rotation_routes)


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

        token = RotationSessionManager.mint()
        url = f"{cfg.public_base_url}/rotation?token={token}"
        await respond_cv2(
            ctx,
            cv2_notice(
                f"[Open the rotation editor here]({url}) — it lists every rotation "
                "and stays signed in for 2 hours."
            ),
            ephemeral=True,
        )


@rotation.register
class ImportFromSheet(
    lb.SlashCommand,
    name="import_from_sheet",
    description="One-shot import of the live Google Sheet into the DB JSON store",
):
    type = lb.string(
        "type",
        "Which rotation post to import (lost_sector or xur_location are sheet-backed)",
        default="lost_sector",
    )

    @lb.invoke
    async def invoke(self, ctx: lb.Context) -> None:
        post_type = str(self.type).strip()
        if post_type == "xur_location":
            await _import_xur_location_from_sheet(ctx)
            return
        if post_type != "lost_sector":
            await respond_cv2(
                ctx,
                cv2_error(
                    "Nothing to import",
                    f"`{post_type}` is not backed by a Google Sheet.",
                ),
                ephemeral=True,
            )
            return

        initial = await ctx.respond(
            components=[cv2_notice("Importing from the live Sheet…")],
            flags=h.MessageFlag.IS_COMPONENTS_V2,
            ephemeral=True,
        )

        try:
            sheet_rotation = await asyncio.to_thread(
                sector_accounting.Rotation.from_gspread_url,
                cfg.sheets_ls_url,
                cfg.gsheets_credentials,
                buffer=5,
            )
        except Exception:
            logger.exception("Sheet import failed")
            await ctx.edit_response(
                initial,
                components=[
                    cv2_error("Import failed", "Couldn't read the Sheet — see logs.")
                ],
            )
            return

        doc = sheet_rotation.to_json()
        parity_ok, mismatch = _rendered_parity(sheet_rotation, doc)
        await schemas.RotationData.set_data(post_type, doc)

        note = (
            "rendered parity check passed ✅"
            if parity_ok
            else f"⚠️ parity mismatch on {mismatch} — review before relying on it"
        )
        await ctx.edit_response(
            initial,
            components=[
                cv2_notice(
                    f"Imported **{post_type}** into the DB store "
                    f"({len(doc['sectors'])} sectors); {note}."
                )
            ],
        )


def _rendered_parity(
    sheet_rotation: sector_accounting.Rotation, doc: dict[str, t.Any]
) -> tuple[bool, str | None]:
    """Compare rendered output of the Sheet reader vs. the imported JSON over a window.

    Compares the presence-level rendered fields (names/links/champions/shields/surges),
    not raw attrs equality, since counts intentionally collapse to a present/absent
    sentinel on import.
    """
    json_rotation = sector_accounting.Rotation.from_json(doc, buffer=5)
    base = dt.datetime.now(dt.UTC)

    def render(rotation: sector_accounting.Rotation, date: dt.datetime) -> t.Any:
        try:
            sectors = rotation(date)
        except KeyError:
            return "TBC"
        return [
            (
                s.name,
                s.shortlink_gfx,
                s.expert_data.champions_list,
                s.expert_data.shields_list,
                s.master_data.champions_list,
                s.master_data.shields_list,
            )
            for s in sectors
        ]

    for offset in range(_PARITY_DAYS):
        date = base + dt.timedelta(days=offset)
        if render(sheet_rotation, date) != render(json_rotation, date):
            return False, date.date().isoformat()
    return True, None


async def _import_xur_location_from_sheet(ctx: lb.Context) -> None:
    """One-shot import of the Xûr location worksheet into ``RotationData``."""
    initial = await ctx.respond(
        components=[cv2_notice("Importing Xûr locations from the live Sheet…")],
        flags=h.MessageFlag.IS_COMPONENTS_V2,
        ephemeral=True,
    )

    try:
        sheet_locations = await asyncio.to_thread(
            xur_support_data.XurLocations.from_gspread_url,
            cfg.sheets_ls_url,
            cfg.gsheets_credentials,
        )
    except Exception:
        logger.exception("Xûr location sheet import failed")
        await ctx.edit_response(
            initial,
            components=[
                cv2_error("Import failed", "Couldn't read the Sheet — see logs.")
            ],
        )
        return

    doc = sheet_locations.to_json()
    parity_ok = _xur_location_parity(sheet_locations, doc)
    await schemas.RotationData.set_data("xur_location", doc)

    note = (
        "resolved-parity check passed ✅"
        if parity_ok
        else "⚠️ parity mismatch — review before relying on it"
    )
    await ctx.edit_response(
        initial,
        components=[
            cv2_notice(
                f"Imported **xur_location** into the DB store "
                f"({len(doc['locations'])} locations); {note}."
            )
        ],
    )


def _xur_location_parity(
    sheet_locations: xur_support_data.XurLocations, doc: dict[str, t.Any]
) -> bool:
    """Round-trip the imported doc and compare the *resolved* rendering per key.

    Compares ``str(location)`` (friendly name + link, exactly what the post renders)
    rather than raw attrs, so the blank-string → ``None`` normalisation on import
    doesn't read as a mismatch.
    """
    json_locations = xur_support_data.XurLocations.from_json(doc)
    if set(sheet_locations.keys()) != set(json_locations.keys()):
        return False
    return all(
        str(sheet_locations[key]) == str(json_locations[key]) for key in sheet_locations
    )


loader.command(rotation)
