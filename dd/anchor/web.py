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


class Card(t.NamedTuple):
    """A homepage entry for one web page/tool.

    ``href`` is a same-origin path (e.g. ``/rotation``); ``title``/``description`` are
    dev-authored copy rendered (escaped) into the homepage card grid.
    """

    title: str
    description: str
    href: str


# Homepage cards contributed by feature modules at import time (mirrors
# _route_registrars). Read at request time by the homepage handler, so contribution
# order is irrelevant — the homepage sorts for display.
_cards: list[Card] = []


def register_routes(
    registrar: t.Callable[[aiohttp.web.Application], None],
) -> None:
    """Register a callback that adds routes to the shared app.

    Call at import time (e.g. module top-level). Registrars run when :func:`start`
    builds the app, so registration must happen before the gateway reaches
    ``StartedEvent``.
    """
    _route_registrars.append(registrar)


def register_card(card: Card) -> None:
    """Contribute a card to the web homepage.

    Call at import time alongside :func:`register_routes` so a feature page appears on
    the homepage without the homepage module needing to know about it. Cards are read
    (and sorted) at request time, so registration order does not matter.
    """
    _cards.append(card)


def registered_cards() -> list[Card]:
    """Return the contributed homepage cards (a copy; caller sorts for display)."""
    return list(_cards)


#: Everything under web_static/tests/. Matched ahead of the static mount in `start`.
_TEST_FIXTURE_ROUTE = "/static/tests/{tail:.*}"


#: The response headers every route gets, including ``/static/``.
#:
#: **Why CSP is here.** The mirror log renders *other servers'* captured Discord posts,
#: in the browser (``web_static/cv2_render.js``). That input is controlled by anyone who
#: can post in a mirrored channel. The renderer's own defences are structural — text
#: reaches the DOM through ``textContent``, URLs are ``http(s)``-checked at the one
#: place they become attributes, and a single field reaches ``innerHTML`` carrying only
#: escape-by-construction markdown — but the markdown tokenizer is hand-rolled, and the
#: comment at ``cv2_model.js``'s ``INLINE`` records that its predecessor really did
#: mangle escaping on real posts. ``script-src 'self'`` is what caps the cost of that
#: happening again at defacement, instead of script execution in an owner's session on a
#: cookie-authed app whose routes publish to Discord.
#:
#: Directive notes, since the shape is deliberate:
#:
#: - ``default-src 'none'`` — the app loads nothing in the unlisted categories (fonts,
#:   media, workers, frames), so an accidental future dependency fails loudly rather
#:   than riding a permissive default. Subsumes ``object-src 'none'``.
#: - ``style-src`` keeps ``'unsafe-inline'`` deliberately: ``charts.js`` and
#:   ``mirror_log.js`` build ``style=`` attributes into markup, for tooltip swatches
#:   and progress-bar widths. Removing it means refactoring those, and what it
#:   concedes to an attacker who already has HTML injection is CSS-based exfiltration
#:   of a DOM holding no secrets (the session cookie is HttpOnly). Not worth it here.
#: - ``img-src ... https:`` is the mirrored-post reality: a captured post embeds images
#:   on any host, so a host list would be fiction. It still excludes ``data:`` and
#:   ``blob:``.
#: - ``base-uri 'none'`` stops injected markup retargeting every ``/static/*.js``
#:   path, and ``form-action 'self'`` blunts the phishing-form variant that survives
#:   CSP — both are one token against attacks the threat model actually has.
_CSP = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' https:; "
    "connect-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

SECURITY_HEADERS = {
    "Content-Security-Policy": _CSP,
    # A rendered post loads images from arbitrary third-party hosts, and each of those
    # requests would otherwise hand the admin app's URL to whoever runs the image host.
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
}


async def _security_headers(
    request: aiohttp.web.Request, response: aiohttp.web.StreamResponse
) -> None:
    """Attach :data:`SECURITY_HEADERS` to every response.

    A response hook rather than a middleware, and that is load-bearing: :func:`start`
    fails closed on ``app.middlewares`` being empty, because the auth middleware is this
    app's only security boundary. A second middleware would satisfy that check even when
    ``web_auth`` failed to load — silently reopening the hole the guard exists to close.

    Every response, including ``/static/``: the static mount is allowlisted
    unauthenticated so a page's assets load before sign-in, and it serves the raw page
    templates too (``/static/editor.html`` renders).
    """
    response.headers.update(SECURITY_HEADERS)


async def _hide_test_fixtures(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """404 the browser-test fixtures, which are not app assets.

    aiohttp's static handler serves a directory wholesale, and the auth middleware
    (``web_auth``) allowlists ``/static/`` so a page's css/js can load before sign-in.
    Together those would publish ``tests/builder_harness.html`` — a fully working CV2
    builder with no auth and no database behind it. The tests load it over ``file://``,
    so nothing needs it served.
    """
    raise aiohttp.web.HTTPNotFound()


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

    # Registered BEFORE the static route below, because the router matches in
    # registration order — see _hide_test_fixtures for why.
    app.router.add_route("*", _TEST_FIXTURE_ROUTE, _hide_test_fixtures)

    # Serve the split editor assets (css/js) so pages can <link>/<script> them instead
    # of inlining. The /static/ prefix is distinct from every feature route (/rotation…,
    # OAuth callback), so it can't collide.
    app.router.add_static("/static/", _WEB_STATIC_DIR)

    # Force browsers to revalidate the static assets on every load. aiohttp's static
    # handler sends only ETag/Last-Modified (no Cache-Control), so browsers apply
    # heuristic caching and can hold a stale /static/shared.css across a deploy. The
    # page HTML now depends on the CSS custom properties defined in shared.css, so a
    # stale copy silently breaks every var() reference (missing borders/toggles).
    # "no-cache" keeps the file cached but requires a conditional GET each load — the
    # ETag makes the common case a cheap 304, and a deploy is picked up immediately.
    async def _revalidate_static(
        request: aiohttp.web.Request, response: aiohttp.web.StreamResponse
    ) -> None:
        if request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"

    app.on_response_prepare.append(_revalidate_static)
    app.on_response_prepare.append(_security_headers)

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
