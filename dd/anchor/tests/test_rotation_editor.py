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

# Rotation editor: session manager + the aiohttp route handlers (homepage / GET page /
# preview / save), exercised against the SQLite test DB from conftest with lightweight
# fake requests (no live server). Auth is the rotation_session cookie.

import datetime as dt
import typing as t

import aiohttp.web
import pytest

from dd.anchor.extensions import rotation_editor as editor
from dd.common import (
    rotation_schema as rs,
    schemas,
)

pytestmark = pytest.mark.asyncio

ZONES = rs.LOST_SECTOR_ZONES


def _doc(first: str = "Alpha") -> dict[str, t.Any]:
    return {
        "version": 1,
        "reference_date": "2023-07-20",
        "schedule": {z: [first] for z in ZONES},
        "sectors": [
            {
                "name": first,
                "shortlink_gfx": "https://x/a",
                "expert": {"champions": ["Barrier"], "shields": ["Arc"]},
                "master": {"champions": [], "shields": []},
            }
        ],
    }


def _xur_doc(name: str = "Nessus, Watcher's Grave") -> dict[str, t.Any]:
    return {
        "version": 1,
        "locations": [
            {
                "api_location_name": name,
                "friendly_location_name": "Watcher's Grave, Nessus",
                "link": "https://kyber3000.com/x",
            }
        ],
    }


class _FakeRequest:
    """Minimal aiohttp.web.Request stand-in for the handlers."""

    def __init__(
        self,
        query: dict[str, str] | None = None,
        body: t.Any = None,
        *,
        cookies: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        raise_json: bool = False,
    ) -> None:
        self.query = query or {}
        self.cookies = cookies or {}
        self.headers = headers or {}
        self._body = body
        self._raise_json = raise_json

    async def json(self) -> t.Any:
        if self._raise_json:
            raise ValueError("bad json")
        return self._body


def _req(
    query: dict[str, str] | None = None,
    body: t.Any = None,
    *,
    cookies: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    raise_json: bool = False,
) -> aiohttp.web.Request:
    return t.cast(
        aiohttp.web.Request,
        _FakeRequest(
            query=query,
            body=body,
            cookies=cookies,
            headers=headers,
            raise_json=raise_json,
        ),
    )


def _cookies(token: str) -> dict[str, str]:
    return {editor._SESSION_COOKIE: token}


# --- session manager -------------------------------------------------------------


async def test_session_resolves_until_burned():
    token = editor.RotationSessionManager.mint()
    assert editor.RotationSessionManager.resolve(token)
    editor.RotationSessionManager.burn(token)
    assert not editor.RotationSessionManager.resolve(token)
    assert not editor.RotationSessionManager.resolve("never-minted")


async def test_session_expiry():
    expired = editor.RotationSessionManager.mint()
    editor.RotationSessionManager._sessions[expired] = dt.datetime.now() - dt.timedelta(
        seconds=1
    )
    assert not editor.RotationSessionManager.resolve(expired)


# --- GET /rotation (homepage) ----------------------------------------------------


async def test_home_entry_token_sets_cookie_and_redirects():
    token = editor.RotationSessionManager.mint()
    resp = await editor._handle_home_get(_req(query={"token": token}))
    assert resp.status == 302
    assert resp.headers.get("Location") == "/rotation"
    # The entry token is stored back as the session cookie.
    assert resp.cookies[editor._SESSION_COOKIE].value == token


async def test_home_entry_rejects_bad_token():
    resp = await editor._handle_home_get(_req(query={"token": "nope"}))
    assert resp.status == 401


async def test_home_lists_all_rotation_types_with_cookie():
    token = editor.RotationSessionManager.mint()
    resp = await editor._handle_home_get(_req(cookies=_cookies(token)))
    assert resp.status == 200
    body = resp.text or ""
    # Every registered rotation type is linked, and a friendly title renders.
    for slug in rs.ROTATION_SCHEMAS:
        assert f"type={slug}" in body
    assert "Lost sector rotation" in body


async def test_home_without_cookie_is_401():
    resp = await editor._handle_home_get(_req())
    assert resp.status == 401


# --- GET /rotation/edit ----------------------------------------------------------


async def test_edit_get_renders_page_with_cookie():
    token = editor.RotationSessionManager.mint()
    resp = await editor._handle_edit_get(
        _req(query={"type": "lost_sector"}, cookies=_cookies(token))
    )
    assert resp.status == 200
    assert resp.content_type == "text/html"
    body = resp.text
    assert body is not None
    assert "/*__BOOTSTRAP__*/ null" not in body
    assert "lost_sector" in body
    # Cookie carries auth: the token must not be embedded in the page.
    assert token not in body


async def test_edit_get_without_cookie_is_401():
    resp = await editor._handle_edit_get(_req(query={"type": "lost_sector"}))
    assert resp.status == 401


async def test_edit_get_unknown_type_is_404():
    token = editor.RotationSessionManager.mint()
    resp = await editor._handle_edit_get(
        _req(query={"type": "nope"}, cookies=_cookies(token))
    )
    assert resp.status == 404


# --- POST /rotation/preview ------------------------------------------------------


async def test_preview_renders_valid_document():
    token = editor.RotationSessionManager.mint()
    resp = await editor._handle_preview(
        _req(body={"type": "lost_sector", "data": _doc()}, cookies=_cookies(token))
    )
    assert resp.status == 200
    assert "Alpha" in (resp.text or "")


async def test_preview_rejects_invalid_document():
    token = editor.RotationSessionManager.mint()
    bad = _doc()
    bad["reference_date"] = "not-a-date"
    resp = await editor._handle_preview(
        _req(body={"type": "lost_sector", "data": bad}, cookies=_cookies(token))
    )
    assert resp.status == 400


async def test_preview_without_cookie_is_401():
    resp = await editor._handle_preview(
        _req(body={"type": "lost_sector", "data": _doc()})
    )
    assert resp.status == 401


# --- POST /rotation/edit ---------------------------------------------------------


async def test_save_persists_and_keeps_session_live():
    token = editor.RotationSessionManager.mint()
    resp = await editor._handle_edit_post(
        _req(
            body={"type": "lost_sector", "data": _doc("Saved")},
            cookies=_cookies(token),
        )
    )
    assert resp.status == 200
    # Session is NOT burned on save — the owner keeps editing.
    assert editor.RotationSessionManager.resolve(token)
    stored = await schemas.RotationData.get_data("lost_sector")
    assert stored is not None
    assert stored["sectors"][0]["name"] == "Saved"
    # A second save in the same session still works.
    resp2 = await editor._handle_edit_post(
        _req(
            body={"type": "lost_sector", "data": _doc("Again")},
            cookies=_cookies(token),
        )
    )
    assert resp2.status == 200
    stored2 = await schemas.RotationData.get_data("lost_sector")
    assert stored2 is not None
    assert stored2["sectors"][0]["name"] == "Again"


async def test_save_rejects_invalid_document_without_writing():
    token = editor.RotationSessionManager.mint()
    bad = _doc()
    del bad["sectors"]
    resp = await editor._handle_edit_post(
        _req(body={"type": "lost_sector", "data": bad}, cookies=_cookies(token))
    )
    assert resp.status == 400
    assert editor.RotationSessionManager.resolve(token)


async def test_save_without_cookie_is_401():
    resp = await editor._handle_edit_post(
        _req(body={"type": "lost_sector", "data": _doc()})
    )
    assert resp.status == 401


async def test_malformed_body_is_a_400():
    token = editor.RotationSessionManager.mint()
    resp = await editor._handle_edit_post(
        _req(cookies=_cookies(token), body=None, raise_json=True)
    )
    assert resp.status == 400
    assert editor.RotationSessionManager.resolve(token)


# --- CSRF / origin ----------------------------------------------------------------


async def test_cross_origin_post_refused(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(editor.cfg, "public_base_url", "https://anchor.example")
    token = editor.RotationSessionManager.mint()
    resp = await editor._handle_edit_post(
        _req(
            body={"type": "lost_sector", "data": _doc()},
            cookies=_cookies(token),
            headers={"Origin": "https://evil.example"},
        )
    )
    assert resp.status == 403


async def test_same_origin_post_allowed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(editor.cfg, "public_base_url", "https://anchor.example")
    token = editor.RotationSessionManager.mint()
    resp = await editor._handle_edit_post(
        _req(
            body={"type": "lost_sector", "data": _doc("OK")},
            cookies=_cookies(token),
            headers={"Origin": "https://anchor.example"},
        )
    )
    assert resp.status == 200


# --- xur_location (a second post type through the same handlers) ------------------


async def test_default_doc_for_xur_location():
    assert editor._default_doc("xur_location") == {"version": 1, "locations": []}


async def test_xur_preview_renders_resolved_locations():
    token = editor.RotationSessionManager.mint()
    resp = await editor._handle_preview(
        _req(body={"type": "xur_location", "data": _xur_doc()}, cookies=_cookies(token))
    )
    assert resp.status == 200
    body = resp.text or ""
    # Friendly name (apostrophe HTML-escaped) + the link both render.
    assert "Grave, Nessus" in body
    assert "https://kyber3000.com/x" in body


async def test_xur_save_persists_via_session():
    token = editor.RotationSessionManager.mint()
    resp = await editor._handle_edit_post(
        _req(
            body={"type": "xur_location", "data": _xur_doc("Tower")},
            cookies=_cookies(token),
        )
    )
    assert resp.status == 200
    assert editor.RotationSessionManager.resolve(token)
    stored = await schemas.RotationData.get_data("xur_location")
    assert stored is not None
    assert stored["locations"][0]["api_location_name"] == "Tower"


async def test_xur_save_rejects_invalid_document_without_writing():
    token = editor.RotationSessionManager.mint()
    bad = _xur_doc()
    del bad["locations"]
    resp = await editor._handle_edit_post(
        _req(body={"type": "xur_location", "data": bad}, cookies=_cookies(token))
    )
    assert resp.status == 400
    assert editor.RotationSessionManager.resolve(token)


async def test_xur_location_parity_matches_after_round_trip():
    from dd.sector_accounting.xur import XurLocations

    locs = XurLocations.from_json(_xur_doc())
    assert editor._xur_location_parity(locs, locs.to_json())
