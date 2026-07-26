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

"""Mirror delivery log page for the anchor web control panel.

An owner-only page (linked from the control-panel homepage via
:func:`web.register_card`) that renders the durable ``mirror_delivery`` ledger — the
source of truth for how a mirrored announcement fanned out to its follower channels. It
is the web-native replacement for the beacon Discord "progress card": because it is
rendered on demand from the DB, there is no long-lived Discord message to supersede,
cancel or freeze.

Two routes, mirroring the ``/stats`` shell + JSON pattern:

- ``GET /mirror-logs`` serves the static shell (``web_static/mirror_log.html``); the
  page fetches its data and renders everything client-side (``mirror_log.js``).
- ``GET /mirror-logs/data`` returns recent runs as JSON, read entirely from the shared
  DB (no Discord API calls). ``?src=<src_msg_id>`` returns one run's per-destination
  rows for the expandable detail view.

Discord snowflake ids exceed JavaScript's safe-integer range, so ids are emitted as
strings; ledger timestamps are naive-UTC wall clocks, stamped UTC here so the browser
parses them unambiguously. Authentication is the shared Discord-OAuth middleware
(``web_auth``), so this module carries no auth code.
"""

import datetime as dt
import logging
from pathlib import Path

import aiohttp.web
import lightbulb as lb

from ...common import schemas
from ...common.utils import followable_name
from .. import web

logger = logging.getLogger(__name__)

# No commands or listeners live here, but load_extensions_strict requires every
# extension module to expose a Loader, so define an (empty) one.
loader = lb.Loader()

_PAGE_HTML_PATH = (
    Path(__file__).resolve().parent.parent / "web_static" / "mirror_log.html"
)

# How far back the run list reaches, and the per-view caps. The window keeps the query
# on the ledger's created_at prune index; the caps bound the JSON payload.
_WINDOW_DAYS = 30
_RUN_LIMIT = 50
_ROW_LIMIT = 500


def _iso_utc(value: dt.datetime | None) -> str | None:
    """Stamp a naive-UTC ledger datetime as UTC ISO-8601 (or pass through ``None``)."""
    return value.replace(tzinfo=dt.UTC).isoformat() if value is not None else None


async def _collect_runs() -> dict:
    runs = await schemas.MirrorDelivery.recent_runs(
        limit=_RUN_LIMIT, within_days=_WINDOW_DAYS
    )
    for run in runs:
        # Resolve the source channel to its configured feed name (else None → the page
        # falls back to the id). followable_name returns the id itself when unknown.
        name = followable_name(id=run["src_ch_id"])
        run["src_name"] = name if isinstance(name, str) else None
        run["src_msg_id"] = str(run["src_msg_id"])
        run["src_ch_id"] = str(run["src_ch_id"])
        run["started"] = _iso_utc(run["started"])
        run["last_at"] = _iso_utc(run["last_at"])
    return {"window_days": _WINDOW_DAYS, "run_limit": _RUN_LIMIT, "runs": runs}


async def _collect_detail(src_msg_id: int) -> dict:
    rows = await schemas.MirrorDelivery.run_rows(src_msg_id, limit=_ROW_LIMIT)
    for row in rows:
        row["dest_ch_id"] = str(row["dest_ch_id"])
        row["dest_msg_id"] = (
            str(row["dest_msg_id"]) if row["dest_msg_id"] is not None else None
        )
        row["dest_server_id"] = (
            str(row["dest_server_id"]) if row["dest_server_id"] is not None else None
        )
        row["created_at"] = _iso_utc(row["created_at"])
        row["finished_at"] = _iso_utc(row["finished_at"])
    return {
        "src_msg_id": str(src_msg_id),
        "rows": rows,
        "truncated": len(rows) >= _ROW_LIMIT,
    }


async def _handle_page(request: aiohttp.web.Request) -> aiohttp.web.Response:
    # Auth is enforced by the web_auth middleware; this just serves the shell.
    return aiohttp.web.Response(
        text=_PAGE_HTML_PATH.read_text(encoding="utf-8"), content_type="text/html"
    )


async def _handle_data(request: aiohttp.web.Request) -> aiohttp.web.Response:
    src = request.query.get("src")
    if src is not None:
        try:
            src_msg_id = int(src)
        except ValueError:
            raise aiohttp.web.HTTPBadRequest(text="src must be an integer") from None
        return aiohttp.web.json_response(await _collect_detail(src_msg_id))
    return aiohttp.web.json_response(await _collect_runs())


def register_mirror_log_routes(app: aiohttp.web.Application) -> None:
    """Add the mirror-log routes to the shared persistent app."""
    app.router.add_get("/mirror-logs", _handle_page)
    app.router.add_get("/mirror-logs/data", _handle_data)


web.register_routes(register_mirror_log_routes)
web.register_card(
    web.Card(
        "Mirror logs",
        "How each mirrored post fanned out to its follower channels",
        "/mirror-logs",
    )
)
