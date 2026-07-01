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

# Rotation editor: token manager + the aiohttp route handlers (GET page / preview /
# save), exercised against the SQLite test DB from conftest with lightweight fake
# requests (no live server).

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


class _FakeRequest:
    """Minimal aiohttp.web.Request stand-in for the handlers."""

    def __init__(
        self,
        query: dict[str, str] | None = None,
        body: t.Any = None,
        *,
        raise_json: bool = False,
    ) -> None:
        self.query = query or {}
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
    raise_json: bool = False,
) -> aiohttp.web.Request:
    return t.cast(
        aiohttp.web.Request,
        _FakeRequest(query=query, body=body, raise_json=raise_json),
    )


# --- token manager ---------------------------------------------------------------


async def test_token_resolves_only_for_its_type():
    token = editor.RotationEditTokenManager.mint("lost_sector")
    assert editor.RotationEditTokenManager.resolve(token, "lost_sector")
    assert not editor.RotationEditTokenManager.resolve(token, "other_type")
    assert not editor.RotationEditTokenManager.resolve("never-minted", "lost_sector")


async def test_token_expiry_and_burn():
    token = editor.RotationEditTokenManager.mint("lost_sector")
    editor.RotationEditTokenManager.burn(token)
    assert not editor.RotationEditTokenManager.resolve(token, "lost_sector")

    expired = editor.RotationEditTokenManager.mint("lost_sector")
    editor.RotationEditTokenManager._tokens[expired] = (
        "lost_sector",
        dt.datetime.now() - dt.timedelta(seconds=1),
    )
    assert not editor.RotationEditTokenManager.resolve(expired, "lost_sector")


# --- GET /rotation/edit ----------------------------------------------------------


async def test_edit_get_renders_page_with_injected_bootstrap():
    token = editor.RotationEditTokenManager.mint("lost_sector")
    resp = await editor._handle_edit_get(
        _req(query={"type": "lost_sector", "token": token})
    )
    assert resp.status == 200
    assert resp.content_type == "text/html"
    body = resp.text
    assert body is not None
    # Placeholder substituted, type present.
    assert "/*__BOOTSTRAP__*/ null" not in body
    assert "lost_sector" in body


async def test_edit_get_rejects_bad_token():
    resp = await editor._handle_edit_get(
        _req(query={"type": "lost_sector", "token": "nope"})
    )
    assert resp.status == 401


# --- POST /rotation/preview ------------------------------------------------------


async def test_preview_renders_valid_document():
    token = editor.RotationEditTokenManager.mint("lost_sector")
    resp = await editor._handle_preview(
        _req(body={"token": token, "type": "lost_sector", "data": _doc()})
    )
    assert resp.status == 200
    body = resp.text
    assert body is not None
    assert "Alpha" in body


async def test_preview_rejects_invalid_document():
    token = editor.RotationEditTokenManager.mint("lost_sector")
    bad = _doc()
    bad["reference_date"] = "not-a-date"
    resp = await editor._handle_preview(
        _req(body={"token": token, "type": "lost_sector", "data": bad})
    )
    assert resp.status == 400


async def test_preview_rejects_bad_token():
    resp = await editor._handle_preview(
        _req(body={"token": "nope", "type": "lost_sector", "data": _doc()})
    )
    assert resp.status == 401


# --- POST /rotation/edit ---------------------------------------------------------


async def test_save_persists_and_burns_token():
    token = editor.RotationEditTokenManager.mint("lost_sector")
    resp = await editor._handle_edit_post(
        _req(body={"token": token, "type": "lost_sector", "data": _doc("Saved")})
    )
    assert resp.status == 200
    assert not editor.RotationEditTokenManager.resolve(token, "lost_sector")
    stored = await schemas.RotationData.get_data("lost_sector")
    assert stored is not None
    assert stored["sectors"][0]["name"] == "Saved"


async def test_save_rejects_invalid_document_without_writing():
    token = editor.RotationEditTokenManager.mint("lost_sector")
    bad = _doc()
    del bad["sectors"]
    resp = await editor._handle_edit_post(
        _req(body={"token": token, "type": "lost_sector", "data": bad})
    )
    assert resp.status == 400
    # Token not burned on a rejected save, so the user can retry.
    assert editor.RotationEditTokenManager.resolve(token, "lost_sector")


async def test_save_rejects_bad_token():
    resp = await editor._handle_edit_post(
        _req(body={"token": "nope", "type": "lost_sector", "data": _doc()})
    )
    assert resp.status == 401


async def test_malformed_body_is_a_400():
    token = editor.RotationEditTokenManager.mint("lost_sector")
    resp = await editor._handle_edit_post(_req(body=None, raise_json=True))
    assert resp.status == 400
    # Token unused; still valid.
    assert editor.RotationEditTokenManager.resolve(token, "lost_sector")
