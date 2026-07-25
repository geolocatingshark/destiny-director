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

# Mirror-log page: /mirror-logs/data returns the ledger as JSON (recent runs, or one
# run's rows for ?src=), /mirror-logs serves the shell, and the homepage card is
# registered. Exercised with a fake request (no live server); auth is the web_auth
# middleware, tested in test_web_auth.py. Confirms the web layer's JSON shaping — ids as
# strings, ledger datetimes stamped UTC — on top of the query tests in
# dd/beacon/tests/test_mirror_log_queries.py.

import asyncio
import json
import types
import typing as t

import aiohttp.web
import pytest
from sqlalchemy import delete

from dd.anchor import web
from dd.anchor.extensions import mirror_log
from dd.common import schemas
from dd.common.schemas import DeliveryState, MirrorDelivery

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean_ledger() -> t.Iterator[None]:
    """Start each test from an empty mirror_delivery table (session-scoped DB)."""

    async def _clear() -> None:
        async with schemas.db_session() as session, session.begin():
            await session.execute(delete(MirrorDelivery))

    asyncio.run(_clear())
    yield


def _as_request(query: dict | None = None) -> aiohttp.web.Request:
    return t.cast(aiohttp.web.Request, types.SimpleNamespace(query=query or {}))


def _text(resp: aiohttp.web.Response) -> str:
    assert resp.text is not None
    return resp.text


async def _seed(rows: list[dict]) -> None:
    async with schemas.db_session() as session, session.begin():
        for r in rows:
            session.add(MirrorDelivery(**r))


def _base(src_msg_id: int, dest_ch_id: int, **over: object) -> dict:
    now = schemas._utcnow()
    row: dict[str, object] = dict(
        src_msg_id=src_msg_id,
        dest_ch_id=dest_ch_id,
        src_ch_id=555,
        state=DeliveryState.DELIVERED.value,
        created_at=now,
        finished_at=now,
        due_at=now,
        dest_msg_id=None,
        desired_version=1,
        applied_version=1,
        attempts=0,
        deleted=False,
    )
    row.update(over)
    return row


@pytest.mark.integration
async def test_data_endpoint_returns_runs_json_shaped() -> None:
    await _seed(
        [
            _base(1111111111111111111, 10, dest_msg_id=2222222222222222222),
            _base(1111111111111111111, 20, dest_msg_id=2222222222222222223),
        ]
    )

    resp = await mirror_log._handle_data(_as_request())

    assert resp.status == 200
    assert resp.content_type == "application/json"
    payload = json.loads(_text(resp))
    assert payload["window_days"] == mirror_log._WINDOW_DAYS
    (run,) = payload["runs"]
    # Snowflakes survive as strings (JS-safe); timestamps carry a UTC offset.
    assert run["src_msg_id"] == "1111111111111111111"
    assert run["src_ch_id"] == "555"
    assert run["total"] == 2 and run["delivered"] == 2
    assert run["started"].endswith("+00:00")
    assert run["last_at"].endswith("+00:00")


@pytest.mark.integration
async def test_data_endpoint_detail_by_src() -> None:
    await _seed(
        [
            _base(
                777,
                20,
                state=DeliveryState.FAILED.value,
                last_error_ref="PERM01",
                last_error_class="PERMANENT",
                last_error_msg="Missing Access",
                finished_at=None,
            ),
            _base(777, 10, dest_msg_id=999),
        ]
    )

    resp = await mirror_log._handle_data(_as_request({"src": "777"}))

    payload = json.loads(_text(resp))
    assert payload["src_msg_id"] == "777"
    assert payload["truncated"] is False
    first = payload["rows"][0]
    assert first["state"] == "FAILED"  # failures first
    assert first["error_ref"] == "PERM01"
    assert first["dest_ch_id"] == "20"
    delivered = next(r for r in payload["rows"] if r["state"] == "DELIVERED")
    assert delivered["dest_msg_id"] == "999"


async def test_data_endpoint_rejects_non_integer_src() -> None:
    with pytest.raises(aiohttp.web.HTTPBadRequest):
        await mirror_log._handle_data(_as_request({"src": "not-a-number"}))


async def test_page_shell_served() -> None:
    resp = await mirror_log._handle_page(_as_request())

    assert resp.status == 200
    assert resp.content_type == "text/html"
    body = _text(resp)
    assert "Mirror logs" in body
    assert "/static/mirror_log.js" in body


async def test_card_is_registered() -> None:
    card = next((c for c in web.registered_cards() if c.title == "Mirror logs"), None)
    assert card is not None
    assert card.href == "/mirror-logs"
