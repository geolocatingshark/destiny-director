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

# Stats page: the /stats/data endpoint returns a well-formed JSON payload read from the
# DB, /stats serves the shell, and the homepage card is registered. Exercised with fake
# request (no live server); auth is the web_auth middleware, tested in test_web_auth.py.

import asyncio
import datetime as dt
import json
import typing as t

import aiohttp.web
import pytest
from sqlalchemy import delete

from dd.anchor import web
from dd.anchor.extensions import stats_page
from dd.common import schemas, settings

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean_tables() -> t.Iterator[None]:
    """Start each test from empty stats tables (session-scoped DB).

    Sync fixture driving the async delete via ``asyncio.run`` — mirrors the anchor test
    suite convention (see test_autopost_settings.py).
    """

    async def _clear() -> None:
        async with schemas.db_session() as session, session.begin():
            await session.execute(delete(schemas.CommandUsage))
            await session.execute(delete(schemas.AutopostDailyStat))
            await session.execute(delete(schemas.ServerStatistics))
            # AutoPostSettings too, since a test here can write a feed's channel row:
            # the DB is session-scoped and shared with every other package, so a row
            # left behind is a row the next file reads as configured. Matches
            # test_autopost_settings.py's fixture, cache reset included — settings
            # caches rows process-wide, so deleting without resetting just means the
            # next test reads the deleted values until the TTL lapses.
            await session.execute(delete(schemas.AutoPostSettings))

    asyncio.run(_clear())
    settings.reset_cache_for_tests()
    yield
    # Teardown as well as setup: the setup half protects this file's tests from each
    # other, and this half protects every later file from a test that failed partway
    # through and never reached its own cleanup.
    asyncio.run(_clear())
    settings.reset_cache_for_tests()


def _as_request() -> aiohttp.web.Request:
    return t.cast(aiohttp.web.Request, object())


def _text(resp: aiohttp.web.Response) -> str:
    # Response.text is typed str | None; every handler here sets it, so narrow to str.
    assert resp.text is not None
    return resp.text


@pytest.mark.integration
async def test_data_endpoint_returns_seeded_rows() -> None:
    today = dt.datetime.now(tz=dt.UTC).date()
    async with schemas.db_session() as session, session.begin():
        session.add(schemas.CommandUsage(command_name="xur", date=today, count=5))
        session.add(
            schemas.CommandUsage(
                command_name="xur", date=today - dt.timedelta(days=1), count=3
            )
        )
        session.add(
            schemas.AutopostDailyStat(date=today, feed="xur", kind="follow", count=9)
        )
    await schemas.ServerStatistics.add_server(123456789012345678, 5000)

    resp = await stats_page._handle_data(_as_request())

    assert resp.status == 200
    assert resp.content_type == "application/json"
    payload = json.loads(_text(resp))

    # Command daily rows (name, iso-date, count); xur totals 8 across the two days.
    assert ["xur", today.isoformat(), 5] in payload["commands"]
    assert sum(c for n, _, c in payload["commands"] if n == "xur") == 8
    # Autopost snapshot row.
    assert [today.isoformat(), "xur", "follow", 9] in payload["autoposts"]
    # Snowflake id survives as a string (JS-safe), paired with its population.
    assert ["123456789012345678", 5000] in payload["populations"]
    # current is always a list (empty here — no followable has a DB row in tests).
    assert isinstance(payload["current"], list)


@pytest.mark.integration
async def test_data_endpoint_windows_out_old_rows() -> None:
    today = dt.datetime.now(tz=dt.UTC).date()
    old = today - dt.timedelta(days=stats_page._WINDOW_DAYS + 5)
    async with schemas.db_session() as session, session.begin():
        session.add(schemas.CommandUsage(command_name="ancient", date=old, count=1))
        session.add(
            schemas.AutopostDailyStat(date=old, feed="xur", kind="mirror", count=1)
        )

    payload = json.loads(_text(await stats_page._handle_data(_as_request())))

    assert payload["commands"] == []
    assert payload["autoposts"] == []


async def test_page_shell_served() -> None:
    resp = await stats_page._handle_page(_as_request())

    assert resp.status == 200
    assert resp.content_type == "text/html"
    body = _text(resp)
    assert "Reach &amp; usage" in body
    assert "/static/stats.js" in body


async def test_card_is_registered() -> None:
    card = next((c for c in web.registered_cards() if c.title == "Reach & usage"), None)
    assert card is not None
    assert card.href == "/stats"
    assert card.group is web.CardGroup.CHECK


@pytest.mark.integration
async def test_a_retired_feed_still_gets_a_row_with_its_history() -> None:
    """A feed off the catalog stays selectable, so its past reach is still readable.

    The history was never the missing part — `autoposts` is unfiltered and always
    carried it. What was missing is a row to click: the page builds its feed list from
    `current`, so a retired feed's year of data was indexed and then unreachable, and
    the all-feeds line dropped on the retirement date with nothing to blame.
    """
    today = dt.datetime.now(tz=dt.UTC).date()
    await schemas.AutoPostSettings.set_value("weekly_nightfall_channel", "77")
    await schemas.AutoPostSettings.set_value("lost_sector_channel", "42")
    await settings.preload()
    async with schemas.db_session() as session, session.begin():
        session.add(
            schemas.AutopostDailyStat(
                date=today, feed="weekly_nightfall", kind="follow", count=4
            )
        )

    payload = json.loads(_text(await stats_page._handle_data(_as_request())))
    rows = {row["feed"]: row for row in payload["current"]}

    assert [today.isoformat(), "weekly_nightfall", "follow", 4] in payload["autoposts"]
    assert rows["weekly_nightfall"]["retired"] is True
    # Named off its own catalog entry, which is what keeping the entry buys.
    assert rows["weekly_nightfall"]["name"] == "Weekly Nightfall"
    # A live feed is unaffected and never mislabelled as retired.
    assert rows["lost_sector"]["retired"] is False
    assert rows["lost_sector"]["name"] == "Lost Sector"
