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

# Autopost settings page: render reflects the AutoPostSettings rows, save persists via
# the model, unknown keys are ignored, and the homepage card is registered. Exercised
# with a fake request (no live server); auth is the web_auth middleware, covered in
# test_web_auth.py, so the handlers assume an already-authenticated request.

import asyncio
import html
import typing as t

import aiohttp.web
import pytest
from sqlalchemy import delete

from dd.anchor import web
from dd.anchor.extensions import autopost_settings as aps
from dd.common import schemas

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean_settings() -> t.Iterator[None]:
    """Start each test from an empty auto_post_settings table (session-scoped DB).

    Sync fixture driving the async delete via ``asyncio.run`` — mirrors conftest's DB
    setup; the anchor test suite avoids async fixtures.
    """

    async def _clear() -> None:
        async with schemas.db_session() as session, session.begin():
            await session.execute(delete(schemas.AutoPostSettings))

    asyncio.run(_clear())
    yield


class _FakeRequest:
    """Minimal aiohttp.web.Request stand-in exposing an awaitable ``.json()``."""

    def __init__(self, payload: object, *, raise_on_json: bool = False) -> None:
        self._payload = payload
        self._raise = raise_on_json

    async def json(self) -> object:
        if self._raise:
            raise ValueError("bad body")
        return self._payload


def _as_request(req: _FakeRequest) -> aiohttp.web.Request:
    return t.cast(aiohttp.web.Request, req)


# --- rendering --------------------------------------------------------------------


@pytest.mark.integration
async def test_render_reflects_db_state() -> None:
    await schemas.AutoPostSettings.set_enabled("lost_sector", True)
    await schemas.AutoPostSettings.set_enabled("xur", False)

    html_out = await aps._render_html()

    # An enabled row renders a checked box; a disabled row renders unchecked.
    assert 'data-slug="lost_sector" checked' in html_out
    assert 'data-slug="xur" checked' not in html_out
    assert 'data-slug="xur"' in html_out
    # Every known toggle appears with its label + description, and rows are switches.
    # Compare against the escaped copy — descriptions carry apostrophes/em-dashes.
    for setting in aps._SETTINGS:
        assert f'data-slug="{setting.slug}"' in html_out
        assert html.escape(setting.label) in html_out
        assert html.escape(setting.desc) in html_out
    assert 'class="switch"' in html_out
    # One .group box per top-level feed; sub-toggles share their parent's box.
    assert html_out.count('class="group"') == sum(
        1 for s in aps._SETTINGS if not s.sub
    )
    assert aps._TOGGLES_PLACEHOLDER not in html_out


@pytest.mark.integration
async def test_render_missing_row_is_unchecked() -> None:
    # No rows seeded → every toggle renders unchecked (producers treat None as off).
    html_out = await aps._render_html()

    assert " checked" not in html_out


@pytest.mark.integration
async def test_handle_get_returns_html_response() -> None:
    resp = await aps._handle_get(_as_request(_FakeRequest(None)))

    assert resp.status == 200
    assert resp.content_type == "text/html"
    assert resp.text is not None
    assert 'data-slug="lost_sector"' in resp.text


# --- saving -----------------------------------------------------------------------


@pytest.mark.integration
async def test_handle_save_persists_toggles() -> None:
    req = _FakeRequest({"settings": {"lost_sector": True, "xur": False}})

    resp = await aps._handle_save(_as_request(req))

    assert resp.status == 200
    assert await schemas.AutoPostSettings.get_enabled("lost_sector") is True
    assert await schemas.AutoPostSettings.get_enabled("xur") is False


@pytest.mark.integration
async def test_handle_save_ignores_unknown_slugs() -> None:
    req = _FakeRequest({"settings": {"not_a_feed": True, "ada": True}})

    resp = await aps._handle_save(_as_request(req))

    assert resp.status == 200
    # The known slug is written; the unknown one never creates a row.
    assert await schemas.AutoPostSettings.get_enabled("ada") is True
    assert await schemas.AutoPostSettings.get_enabled("not_a_feed") is None


@pytest.mark.integration
async def test_handle_save_coerces_truthy_values() -> None:
    # The client sends booleans, but bool() must coerce anything the JSON carries.
    req = _FakeRequest({"settings": {"eververse": 1, "portal_ops": 0}})

    await aps._handle_save(_as_request(req))

    assert await schemas.AutoPostSettings.get_enabled("eververse") is True
    assert await schemas.AutoPostSettings.get_enabled("portal_ops") is False


async def test_handle_save_rejects_malformed_body() -> None:
    resp = await aps._handle_save(_as_request(_FakeRequest(None, raise_on_json=True)))

    assert resp.status == 400


async def test_handle_save_rejects_non_object_settings() -> None:
    resp = await aps._handle_save(_as_request(_FakeRequest({"settings": "nope"})))

    assert resp.status == 400


# --- homepage card ----------------------------------------------------------------


async def test_card_is_registered() -> None:
    titles = [card.title for card in web.registered_cards()]
    assert "Autopost Settings" in titles
    card = next(c for c in web.registered_cards() if c.title == "Autopost Settings")
    assert card.href == "/autopost_settings"
