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

"""The /static/ mount serves app assets and nothing else.

``web_auth`` allowlists ``/static/`` so a page's css/js can load before sign-in. That
makes ``web_static/`` public, which is right for stylesheets and wrong for
``web_static/tests/`` — ``builder_harness.html`` mounts a fully working CV2 builder
with no auth and no database, purely so a browser test can drive it over ``file://``.
"""

import aiohttp.web
import pytest
from aiohttp.test_utils import TestClient, TestServer

from dd.anchor import web


def _app() -> aiohttp.web.Application:
    """The static routes exactly as :func:`web.start` registers them."""
    app = aiohttp.web.Application()
    app.router.add_route("*", web._TEST_FIXTURE_ROUTE, web._hide_test_fixtures)
    app.router.add_static("/static/", web._WEB_STATIC_DIR)
    app.on_response_prepare.append(web._security_headers)
    return app


@pytest.mark.asyncio
async def test_app_assets_are_served() -> None:
    """The guard must not shadow the real assets it sits in front of."""
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/static/shared.css")
        assert resp.status == 200
        assert "--dur-fast" in await resp.text()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/static/tests/builder_harness.html",
        "/static/tests/cv2_model.test.js",
        "/static/tests/",
    ],
)
async def test_test_fixtures_are_not_served(path: str) -> None:
    """Everything under web_static/tests/ is 404, fixture and test files alike."""
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get(path)
        assert resp.status == 404, f"{path} is reachable — it must not be"


def test_the_harness_the_route_hides_actually_exists() -> None:
    """Guard against the 404 above passing because the file was renamed or moved.

    Without this, deleting the harness would make the security test vacuously green.
    """
    assert (web._WEB_STATIC_DIR / "tests" / "builder_harness.html").is_file()


# --- security headers ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_security_headers_reach_static_assets() -> None:
    """``/static/`` gets them too, and that is the point of the response hook.

    The mount is allowlisted unauthenticated so a page's css/js load before sign-in,
    and it serves the raw page templates as well — ``/static/editor.html`` renders as
    a page. A per-route opt-in would be one route away from missing that.
    """
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/static/shared.css")
        assert resp.status == 200
        for name, value in web.SECURITY_HEADERS.items():
            assert resp.headers[name] == value


def test_the_security_headers_are_pinned() -> None:
    """One place the exact policy is spelled out — the change-review chokepoint.

    Pinned here and nowhere else, so loosening a directive shows up as a deliberate edit
    to this string rather than as a quiet change nobody reads. Two directives are worth
    naming explicitly, since the rest of the suite depends on them holding:

    - ``script-src 'self'`` with no ``'unsafe-inline'`` is what the whole change is for.
      It caps an escaping bug in the shared renderer at defacement rather than script
      execution in an owner session. ``asset_links.test.js`` enforces its precondition
      (no page carries an executable inline script).
    - ``style-src`` deliberately DOES carry ``'unsafe-inline'``: charts.js and
      mirror_log.js build ``style=`` attributes into markup.
    """
    assert web.SECURITY_HEADERS == {
        "Content-Security-Policy": (
            "default-src 'none'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' https:; "
            "connect-src 'self'; "
            "base-uri 'none'; "
            "form-action 'self'; "
            "frame-ancestors 'none'"
        ),
        "Referrer-Policy": "same-origin",
        "X-Content-Type-Options": "nosniff",
    }
