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

# Control panel: the card registry (web.register_card / registered_cards) and the panel
# route handler, exercised with a lightweight fake request (no live server).
# Authentication is handled centrally by the web_auth middleware (covered in
# test_web_auth.py), so the handler assumes an already-authenticated request.

import typing as t

import aiohttp.web
import pytest

from dd.anchor import web
from dd.anchor.extensions import control_panel

pytestmark = pytest.mark.asyncio


@pytest.fixture
def clean_cards() -> t.Iterator[None]:
    """Isolate the module-level card registry so tests don't leak into each other."""
    saved = list(web._cards)
    web._cards.clear()
    try:
        yield
    finally:
        web._cards[:] = saved


class _FakeRequest:
    """Minimal aiohttp.web.Request stand-in — the panel handler reads nothing off it."""


async def test_register_card_appends_and_registered_cards_returns_copy(
    clean_cards: None,
) -> None:
    card = web.Card("Alpha", "first tool", "/alpha")
    web.register_card(card)

    cards = web.registered_cards()
    assert cards == [card]
    # registered_cards returns a copy — mutating it must not touch the registry.
    cards.append(web.Card("Beta", "x", "/beta"))
    assert web.registered_cards() == [card]


async def test_render_lists_cards_href_and_title_sorted(clean_cards: None) -> None:
    # Register out of order; the panel sorts by (title, …) for a stable display.
    web.register_card(web.Card("Weekly Reset", "compose the post", "/weekly_reset"))
    web.register_card(web.Card("Rotation Editor", "edit rotations", "/rotation"))

    html_out = control_panel._render_panel_html()

    assert 'href="/rotation"' in html_out
    assert 'href="/weekly_reset"' in html_out
    assert "Rotation Editor" in html_out
    assert "Weekly Reset" in html_out
    # Sorted: "Rotation Editor" (R) renders before "Weekly Reset" (W).
    assert html_out.index("Rotation Editor") < html_out.index("Weekly Reset")


async def test_render_escapes_html_in_card_fields(clean_cards: None) -> None:
    web.register_card(web.Card("A & <b>", "desc <script>", "/x?a=1&b=2"))

    html_out = control_panel._render_panel_html()

    # The card grid must not contain the raw, unescaped markup we fed in.
    assert "<b>" not in html_out
    assert "<script>" not in html_out
    assert "A &amp; &lt;b&gt;" in html_out
    assert "desc &lt;script&gt;" in html_out
    assert "/x?a=1&amp;b=2" in html_out


async def test_render_empty_registry_does_not_crash(clean_cards: None) -> None:
    html_out = control_panel._render_panel_html()

    assert "No web tools are available." in html_out
    assert "<!--__CARDS__-->" not in html_out


async def test_handle_panel_returns_html_response(clean_cards: None) -> None:
    web.register_card(web.Card("Rotation Editor", "edit rotations", "/rotation"))

    resp = await control_panel._handle_panel(
        t.cast(aiohttp.web.Request, _FakeRequest())
    )

    assert resp.status == 200
    assert resp.content_type == "text/html"
    assert resp.text is not None
    assert 'href="/rotation"' in resp.text
