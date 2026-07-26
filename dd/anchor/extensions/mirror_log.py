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

Three routes, mirroring the ``/stats`` shell + JSON pattern:

- ``GET /mirror-logs`` serves the static shell (``web_static/mirror_log.html``); the
  page fetches its data and renders everything client-side (``mirror_log.js``).
- ``GET /mirror-logs/data`` returns recent runs as JSON, read entirely from the shared
  DB (no Discord API calls). ``?src=<src_msg_id>`` returns that run's captured version
  list for the expandable detail view (the mirrored message itself).
- ``GET /mirror-logs/render?src=<id>&v=<n>`` returns the safe rendered HTML of one
  captured version (see :mod:`dd.anchor.cv2_render`); adding ``&diff=<m>`` returns a
  word-level diff of version ``n`` against version ``m``. Pull/stateless.

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
from ..cv2_render import render_diff, render_snapshot

logger = logging.getLogger(__name__)

# No commands or listeners live here, but load_extensions_strict requires every
# extension module to expose a Loader, so define an (empty) one.
loader = lb.Loader()

_PAGE_HTML_PATH = (
    Path(__file__).resolve().parent.parent / "web_static" / "mirror_log.html"
)

# How far back the run list reaches, and its cap. The window keeps the query on the
# ledger's created_at prune index; the cap bounds the JSON payload.
_WINDOW_DAYS = 30
_RUN_LIMIT = 50


def _iso_utc(value: dt.datetime | None) -> str | None:
    """Stamp a naive-UTC ledger datetime as UTC ISO-8601 (or pass through ``None``)."""
    return value.replace(tzinfo=dt.UTC).isoformat() if value is not None else None


async def _collect_runs() -> dict:
    runs = await schemas.MirrorDelivery.recent_runs(
        limit=_RUN_LIMIT, within_days=_WINDOW_DAYS
    )
    # One batch lookup of each source's latest captured snapshot supplies the run-list
    # summary label + the source guild id for a jump-to-source link (retiring the last
    # bare snowflake). Sources predating the capture deploy simply have no entry.
    latest = await schemas.MirrorMessageVersion.latest_for(
        [run["src_msg_id"] for run in runs]
    )
    for run in runs:
        # Resolve the source channel to its configured feed name (else None → the page
        # falls back to the id). followable_name returns the id itself when unknown.
        name = followable_name(id=run["src_ch_id"])
        run["src_name"] = name if isinstance(name, str) else None
        snap = latest.get(run["src_msg_id"])
        run["summary"] = snap["summary"] if snap else None
        run["src_guild_id"] = (
            str(snap["src_guild_id"])
            if snap and snap["src_guild_id"] is not None
            else None
        )
        run["src_msg_id"] = str(run["src_msg_id"])
        run["src_ch_id"] = str(run["src_ch_id"])
        run["started"] = _iso_utc(run["started"])
        run["last_at"] = _iso_utc(run["last_at"])
    return {"window_days": _WINDOW_DAYS, "run_limit": _RUN_LIMIT, "runs": runs}


async def _collect_detail(src_msg_id: int) -> dict:
    # The detail carries the mirrored *message* (the version render pane) plus the run's
    # failure breakdown — the aggregate "why did it fail" the old progress card showed,
    # grouped by error reference (the per-destination counts come from the run list).
    versions = await schemas.MirrorMessageVersion.versions_for(src_msg_id)
    failures = await schemas.MirrorDelivery.failure_breakdown(src_msg_id)
    return {
        "src_msg_id": str(src_msg_id),
        # Version snapshots power the render pane; empty for sources predating capture.
        "versions": [
            {
                "version": v["version"],
                "captured_at": _iso_utc(v["captured_at"]),
                "summary": v["summary"],
                "kind": v["kind"],
            }
            for v in versions
        ],
        "failures": [
            {"ref": ref, "error_class": err_class, "count": count, "sample": sample}
            for (ref, err_class, count, sample) in failures
        ],
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


def _int_param(request: aiohttp.web.Request, name: str) -> int:
    raw = request.query.get(name)
    if raw is None:
        raise aiohttp.web.HTTPBadRequest(text=f"{name} is required")
    try:
        return int(raw)
    except ValueError:
        raise aiohttp.web.HTTPBadRequest(text=f"{name} must be an integer") from None


async def _handle_render(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """Return the safe rendered HTML for one captured version of a source message.

    ``?src=<id>&v=<n>`` renders version ``n``; adding ``&diff=<m>`` returns a word-level
    diff of version ``n`` against version ``m`` instead. Pull/stateless — no live
    message, no lifecycle. The HTML is pre-escaped by the renderer, safe for the page's
    ``innerHTML`` sink."""
    src_msg_id = _int_param(request, "src")
    version = _int_param(request, "v")
    new = await schemas.MirrorMessageVersion.get_version(src_msg_id, version)
    if new is None:
        raise aiohttp.web.HTTPNotFound(text="No snapshot for that version.")

    diff = request.query.get("diff")
    if diff is not None:
        old = await schemas.MirrorMessageVersion.get_version(
            src_msg_id, _int_param(request, "diff")
        )
        if old is None:
            raise aiohttp.web.HTTPNotFound(
                text="No snapshot for the diff-against version."
            )
        body = render_diff(new["payload"], new["kind"], old["payload"], old["kind"])
    else:
        body = render_snapshot(new["payload"], new["kind"])
    return aiohttp.web.Response(text=body, content_type="text/html")


def register_mirror_log_routes(app: aiohttp.web.Application) -> None:
    """Add the mirror-log routes to the shared persistent app."""
    app.router.add_get("/mirror-logs", _handle_page)
    app.router.add_get("/mirror-logs/data", _handle_data)
    app.router.add_get("/mirror-logs/render", _handle_render)


web.register_routes(register_mirror_log_routes)
web.register_card(
    web.Card(
        "Mirror logs",
        "How each mirrored post fanned out to its follower channels",
        "/mirror-logs",
    )
)
