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

"""Integration tests for the mirror-log version render route + detail/run enrichment.

Seeds ``mirror_message_version`` snapshots and drives ``_handle_render`` (version
render, diff, 404s, bad params) and ``_handle_data`` (detail carries the version list;
the
run list carries the snapshot summary + source guild id for the jump-to-source link).
"""

import asyncio
import json
import types
import typing as t

import aiohttp.web
import pytest
from sqlalchemy import delete

from dd.anchor.extensions import mirror_log
from dd.common import schemas
from dd.common.schemas import (
    DeliveryState,
    MirrorDelivery,
    MirroredChannel,
    MirrorMessageVersion,
    MirrorOperationLog,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _clean() -> t.Iterator[None]:
    async def _clear() -> None:
        async with schemas.db_session() as session, session.begin():
            await session.execute(delete(MirrorMessageVersion))
            await session.execute(delete(MirrorDelivery))
            await session.execute(delete(MirroredChannel))

    asyncio.run(_clear())
    yield


def _as_request(query: dict | None = None) -> aiohttp.web.Request:
    return t.cast(aiohttp.web.Request, types.SimpleNamespace(query=query or {}))


def _cv2(text: str) -> dict:
    return {"components": [{"type": 17, "components": [{"type": 10, "content": text}]}]}


async def _seed_delivery(src_msg_id: int, src_ch_id: int = 555) -> None:
    now = schemas._utcnow()
    async with schemas.db_session() as session, session.begin():
        session.add(
            MirrorDelivery(
                src_msg_id=src_msg_id,
                dest_ch_id=10,
                src_ch_id=src_ch_id,
                state=DeliveryState.DELIVERED.value,
                created_at=now,
                finished_at=now,
                due_at=now,
            )
        )


async def _capture(
    src_msg_id: int, version: int, text: str, *, guild: int | None
) -> None:
    await MirrorMessageVersion.capture(
        src_msg_id=src_msg_id,
        version=version,
        src_guild_id=guild,
        kind="cv2",
        summary=text.splitlines()[0],
        payload=_cv2(text),
    )


async def test_render_route_returns_the_captured_payload() -> None:
    """The route hands over the snapshot; the page draws it.

    Rendering moved to the shared client renderer, so what this has to get right is the
    payload and the kind tag that selects the branch. The render itself is pinned by the
    corpus in ``dd/anchor/preview_fixtures`` — which is also where the injection probes
    live, because this route is the one untrusted sink in the app.
    """
    await _capture(100, 1, "Weekly reset v1", guild=42)

    resp = await mirror_log._handle_render(_as_request({"src": "100", "v": "1"}))

    assert resp.status == 200
    body = json.loads(resp.text or "{}")
    assert body["kind"] == "snapshot"
    assert body["message_kind"] == "cv2"
    assert "Weekly reset v1" in json.dumps(body["payload"])


async def test_render_route_diff_marks_changes() -> None:
    await _capture(200, 1, "alpha beta", guild=None)
    await _capture(200, 2, "alpha gamma", guild=None)

    resp = await mirror_log._handle_render(
        _as_request({"src": "200", "v": "2", "diff": "1"})
    )

    # The alignment stays here (it needs difflib); what ships is its verdict, as
    # pre-split runs the client only has to draw. The rendering of these annotations is
    # pinned by the shared corpus, against what the old Python diff renderer emitted.
    body = json.loads(resp.text or "{}")
    assert body["kind"] == "diff"

    def runs(node: t.Any) -> list[t.Any]:
        """Every word-level run in the tree, wherever the changed leaf sits."""
        if isinstance(node, list):
            return [r for n in node for r in runs(n)]
        if not isinstance(node, dict):
            return []
        out = [r for entry in (node.get("_lines") or []) for r in entry.get("runs", [])]
        return out + runs(node.get("components") or [])

    found = runs(body["diff"]["components"])
    assert {"op": "del", "text": "beta"} in found
    assert {"op": "ins", "text": "gamma"} in found


async def test_render_route_404_for_missing_version() -> None:
    with pytest.raises(aiohttp.web.HTTPNotFound):
        await mirror_log._handle_render(_as_request({"src": "999", "v": "1"}))


async def test_render_route_rejects_bad_params() -> None:
    with pytest.raises(aiohttp.web.HTTPBadRequest):
        await mirror_log._handle_render(_as_request({"src": "x", "v": "1"}))
    with pytest.raises(aiohttp.web.HTTPBadRequest):
        await mirror_log._handle_render(_as_request({"src": "1"}))  # missing v


async def test_detail_payload_lists_versions() -> None:
    await _seed_delivery(300)
    await _capture(300, 1, "first", guild=7)
    await _capture(300, 2, "second", guild=7)

    resp = await mirror_log._handle_data(_as_request({"src": "300"}))
    payload = json.loads(resp.text or "{}")

    versions = payload["versions"]
    assert [v["version"] for v in versions] == [1, 2]  # oldest first
    assert versions[0]["summary"] == "first"
    assert versions[0]["captured_at"].endswith("+00:00")  # UTC-stamped
    assert all("payload" not in v for v in versions)  # listing stays light


async def test_run_list_carries_summary_and_source_link() -> None:
    await _seed_delivery(400, src_ch_id=555)
    await _capture(400, 1, "old", guild=None)
    await _capture(400, 2, "Latest headline", guild=9001)  # newest wins

    resp = await mirror_log._handle_data(_as_request())
    (run,) = json.loads(resp.text or "{}")["runs"]

    assert run["summary"] == "Latest headline"  # from the newest snapshot
    assert run["src_guild_id"] == "9001"  # enables the jump-to-source link


async def test_detail_payload_includes_operations() -> None:
    # The detail must carry each recorded operation's stats (so the version columns show
    # real per-op numbers, not "counts not recorded"). Guards the duplicate-def
    # regression where the payload silently dropped operations.
    now = schemas._utcnow()
    for op, ver in (("create", 1), ("update", 2)):
        await MirrorOperationLog.record(
            src_msg_id=900,
            src_ch_id=1,
            op_type=op,
            version=ver,
            started_at=now,
            finished_at=now,
            total=150,
            delivered=150,
            failed=0,
            cancelled=0,
            attempts=1,
            failure_refs=None,
        )

    resp = await mirror_log._handle_data(_as_request({"src": "900"}))
    payload = json.loads(resp.text or "{}")

    ops = payload["operations"]
    assert [(o["op_type"], o["version"], o["delivered"]) for o in ops] == [
        ("create", 1, 150),
        ("update", 2, 150),
    ]


async def test_detail_payload_includes_failure_breakdown() -> None:
    # The detail carries the grouped failure breakdown (the old progress card's stat),
    # so the web stats block can show *why* destinations failed.
    now = schemas._utcnow()
    async with schemas.db_session() as session, session.begin():
        for dest in (10, 11):
            session.add(
                MirrorDelivery(
                    src_msg_id=800,
                    dest_ch_id=dest,
                    src_ch_id=1,
                    state=DeliveryState.FAILED.value,
                    created_at=now,
                    due_at=now,
                    last_error_ref="PERM01",
                    last_error_class="PERMANENT",
                    last_error_msg="Missing Access",
                )
            )

    resp = await mirror_log._handle_data(_as_request({"src": "800"}))
    payload = json.loads(resp.text or "{}")

    (fail,) = payload["failures"]
    assert fail["ref"] == "PERM01"
    assert fail["count"] == 2  # grouped by error reference
    assert fail["error_class"] == "PERMANENT"
    assert fail["sample"] == "Missing Access"


async def test_run_list_without_snapshots_has_no_summary() -> None:
    await _seed_delivery(
        500
    )  # delivery row but no captured version (pre-deploy source)

    resp = await mirror_log._handle_data(_as_request())
    (run,) = json.loads(resp.text or "{}")["runs"]

    assert run["summary"] is None
    assert run["src_guild_id"] is None  # falls back to the bare msg id in the UI
