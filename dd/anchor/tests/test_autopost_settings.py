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
import re
import typing as t

import aiohttp.web
import pytest
from sqlalchemy import delete

from dd.anchor import autopost, web
from dd.anchor.extensions import autopost_settings as aps
from dd.common import schemas
from dd.hmessage import HMessage

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


async def _noop(**_kwargs: object) -> HMessage:
    """Stand-in constructor; the render path never calls it."""
    raise AssertionError("the settings page must not build a post to render a row")


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
    assert html_out.count('class="group"') == sum(1 for s in aps._SETTINGS if not s.sub)
    assert aps._TOGGLES_PLACEHOLDER not in html_out


@pytest.mark.integration
async def test_render_missing_row_is_unchecked() -> None:
    # No rows seeded → every toggle renders unchecked (producers treat None as off).
    # Matched against the toggles specifically: the send modal's publish checkbox is
    # also `checked` by default, and it is not a setting.
    html_out = await aps._render_html()

    assert not re.search(r'data-slug="[^"]+" checked', html_out)


@pytest.mark.integration
async def test_handle_get_returns_html_response() -> None:
    resp = await aps._handle_get(_as_request(_FakeRequest(None)))

    assert resp.status == 200
    assert resp.content_type == "text/html"
    assert resp.text is not None
    assert 'data-slug="lost_sector"' in resp.text


# --- per-feed actions ---------------------------------------------------------------
#
# Preview and Send now replaced the `/<feed> show` and `send` commands. They render on
# the row itself rather than a per-feed page, and the rendered post shows in a modal —
# so the list stays a list of toggles.


@pytest.fixture
def _registered_feed(monkeypatch: pytest.MonkeyPatch) -> t.Iterator[None]:
    """Register one feed, so the row actions render.

    The real registry is filled by the producer modules at import time; a test that
    relied on some other test having imported them would pass or fail by ordering.
    """
    monkeypatch.setattr(
        autopost,
        "_feeds",
        {
            "lost_sector": autopost.Feed(
                name="lost_sector", channel_id=7, message_constructor_coro=_noop
            )
        },
    )
    yield


@pytest.mark.integration
async def test_feed_rows_carry_both_actions_with_hover_cards(
    _registered_feed: None,
) -> None:
    html_out = await aps._render_html()

    for action in ("preview", "send"):
        assert (
            f'data-action="{action}" data-slug="lost_sector"' in html_out
        ), f"the {action} action is missing from the lost_sector row"
    # Explanations are hover cards, not paragraphs — two labelled buttons do not need
    # two blocks of copy on a page that is otherwise a dense list.
    assert html_out.count("title=") >= 2


@pytest.mark.integration
async def test_sub_settings_and_url_rows_get_no_actions(
    _registered_feed: None,
) -> None:
    # `lost_sector_details` refines its parent and has no producer of its own, and the
    # eververse image URL is a value, not a feed — neither can be previewed or sent.
    html_out = await aps._render_html()

    assert 'data-slug="lost_sector_details"' in html_out  # the row still renders
    for slug in ("lost_sector_details", "xur_default_image", "eververse_image_url"):
        assert f'data-action="preview" data-slug="{slug}"' not in html_out


@pytest.mark.integration
async def test_page_hosts_the_preview_and_send_modals() -> None:
    # The post is drawn in a modal by the shared renderer, so the page must load it and
    # its stylesheet, and the publish choice belongs in the send confirmation rather
    # than sitting pre-set on the page.
    resp = await aps._handle_get(_as_request(_FakeRequest(None)))
    assert resp.text is not None
    body = resp.text

    assert '<dialog class="feedmodal" id="previewDialog">' in body
    assert '<dialog class="feedmodal" id="sendDialog">' in body
    assert 'id="sendPreview"' in body
    assert 'id="publish"' in body
    assert "/static/cv2_render.js" in body
    assert "/static/cv2_preview.css" in body
    # Both preview hosts must opt into the shared styling, or the renderer draws
    # correct DOM with none of its appearance.
    assert body.count('class="modalpreview cv2-preview"') == 2


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


# --- url setting (eververse default image) ----------------------------------------


@pytest.mark.integration
async def test_render_shows_url_field_with_value() -> None:
    await schemas.AutoPostSettings.set_eververse_image_url(
        "https://example.com/banner.png"
    )

    html_out = await aps._render_html()

    # The URL setting renders a text input (not a switch) carrying its saved value.
    assert 'class="urlfield" data-slug="eververse_image_url"' in html_out
    assert 'value="https://example.com/banner.png"' in html_out


@pytest.mark.integration
async def test_handle_save_persists_url_value() -> None:
    req = _FakeRequest(
        {"settings": {"eververse_image_url": "https://example.com/banner.png"}}
    )

    resp = await aps._handle_save(_as_request(req))

    assert resp.status == 200
    assert (
        await schemas.AutoPostSettings.get_eververse_image_url()
        == "https://example.com/banner.png"
    )


@pytest.mark.integration
async def test_handle_save_blank_url_clears_value() -> None:
    await schemas.AutoPostSettings.set_eververse_image_url("https://example.com/a.png")

    resp = await aps._handle_save(
        _as_request(_FakeRequest({"settings": {"eververse_image_url": "  "}}))
    )

    assert resp.status == 200
    # A blank field stores NULL → "no image".
    assert await schemas.AutoPostSettings.get_eververse_image_url() is None


@pytest.mark.integration
async def test_handle_save_rejects_non_http_url() -> None:
    resp = await aps._handle_save(
        _as_request(
            _FakeRequest({"settings": {"eververse_image_url": "ftp://x/y.png"}})
        )
    )

    assert resp.status == 400
    # The whole save aborts before any write — no row is created.
    assert await schemas.AutoPostSettings.get_eververse_image_url() is None


@pytest.mark.integration
async def test_handle_save_rejects_non_string_url() -> None:
    resp = await aps._handle_save(
        _as_request(_FakeRequest({"settings": {"eververse_image_url": 123}}))
    )

    assert resp.status == 400


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
