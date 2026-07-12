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

"""The anchor process's single persistent HTTP server.

Railway exposes exactly one port (``cfg.port``), so anchor runs one aiohttp app that
hosts every HTTP surface: the Bungie OAuth callback (previously a transient server spun
up per ``/bungie login``) and the rotation editor. Feature modules contribute routes by
registering a callback at import time via :func:`register_routes`; the app is built and
started once on ``StartedEvent`` (see ``dd/anchor/__main__.py``) and stopped on
``StoppingEvent``.
"""

import logging
import typing as t
from pathlib import Path

import aiohttp.web

from ..common import cfg

logger = logging.getLogger(__name__)

# The directory of static web assets (editor html/css/js) served under /static/. Derived
# the same way the feature modules resolve their templates (this module lives in
# dd/anchor/, so its parent holds web_static/), not a hardcoded absolute path.
_WEB_STATIC_DIR = Path(__file__).resolve().parent / "web_static"

# Route registrars contributed by feature modules at import time. Applied in order when
# the app is built in start(). Kept as module state so modules stay decoupled from the
# app object and from each other.
_route_registrars: list[t.Callable[[aiohttp.web.Application], None]] = []
_runner: aiohttp.web.AppRunner | None = None


def register_routes(
    registrar: t.Callable[[aiohttp.web.Application], None],
) -> None:
    """Register a callback that adds routes to the shared app.

    Call at import time (e.g. module top-level). Registrars run when :func:`start`
    builds the app, so registration must happen before the gateway reaches
    ``StartedEvent``.
    """
    _route_registrars.append(registrar)


async def start(port: int | None = None) -> None:
    """Build the app from all registered route contributors and start listening."""
    global _runner
    if _runner is not None:
        logger.warning("Anchor web app already started; ignoring duplicate start()")
        return

    app = aiohttp.web.Application()
    for registrar in _route_registrars:
        registrar(app)

    # Fail closed: the auth middleware (dd.anchor.extensions.web_auth) is this app's
    # only security boundary — every feature module deleted its per-handler auth and
    # relies on it being installed here. If no middleware registered (e.g. web_auth
    # failed to import and load_extensions_strict skipped it), refuse to serve rather
    # than expose the editor / weekly-reset form unauthenticated.
    if not app.middlewares:
        raise RuntimeError(
            "Anchor web app has no middleware registered — refusing to start an "
            "unauthenticated web surface (is the web_auth extension loading?)."
        )

    # Serve the split editor assets (css/js) so pages can <link>/<script> them instead
    # of inlining. The /static/ prefix is distinct from every feature route (/rotation…,
    # OAuth callback), so it can't collide.
    app.router.add_static("/static/", _WEB_STATIC_DIR)

    # access_log=None disables aiohttp's default request-line access log. The editor
    # entry links and the OAuth callback carry secrets in the query string
    # (?token=…, ?code=/?state=…); the default log records the full request line, which
    # would leak those to anyone with log-read access (CWE-532). This app logs its own
    # meaningful events via the module logger, so the request log has little value here.
    runner = aiohttp.web.AppRunner(app, access_log=None)
    await runner.setup()
    bind_port = cfg.port if port is None else port
    site = aiohttp.web.TCPSite(runner, "0.0.0.0", bind_port)
    await site.start()
    _runner = runner
    logger.info("Anchor web app listening on 0.0.0.0:%s", bind_port)


async def stop() -> None:
    """Stop the server and release the port (idempotent)."""
    global _runner
    if _runner is None:
        return
    await _runner.shutdown()
    await _runner.cleanup()
    _runner = None
    logger.info("Anchor web app stopped")
