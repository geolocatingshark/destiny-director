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
