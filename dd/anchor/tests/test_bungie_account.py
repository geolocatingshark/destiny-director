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

# Bungie account page: the shell is static, /data reports the link's health from the
# stored credentials, /login redirects to Bungie rather than fetching, and /account
# returns the ids WITHOUT the access token. Exercised with fake requests (no live
# server); auth is the web_auth middleware, covered in test_web_auth.py.

import datetime as dt
import json
import typing as t

import aiohttp.web
import pytest

from dd.anchor.extensions import bungie_account

pytestmark = pytest.mark.asyncio


def _as_request() -> aiohttp.web.Request:
    return t.cast(aiohttp.web.Request, object())


def _text(resp: aiohttp.web.Response) -> str:
    """Response.text is typed ``str | None``; every handler here sets it."""
    assert resp.text is not None
    return resp.text


class _Credentials:
    def __init__(self, refresh_token: str | None, expires: dt.datetime | None) -> None:
        self.refresh_token = refresh_token
        self.refresh_token_expires = expires


def _stub_credentials(monkeypatch: pytest.MonkeyPatch, value: object) -> None:
    async def _get(*_args: object, **_kwargs: object) -> object:
        return value

    monkeypatch.setattr(
        bungie_account.schemas.BungieCredentials, "get_credentials", _get
    )


# --- the page shell ----------------------------------------------------------------


async def test_page_shell_carries_no_inline_script() -> None:
    # script-src 'self' (web.py's CSP) forbids inline script, so the shell must be
    # static and fetch /bungie/data for itself.
    body = _text(await bungie_account._handle_page(_as_request()))
    assert "/static/bungie_account.js" in body
    assert "<script>" not in body


# --- link status --------------------------------------------------------------------


async def test_data_reports_a_healthy_link(monkeypatch: pytest.MonkeyPatch) -> None:
    expires = dt.datetime.now() + dt.timedelta(days=30)
    _stub_credentials(monkeypatch, _Credentials("a-refresh-token", expires))
    payload = json.loads(_text(await bungie_account._handle_data(_as_request())))
    assert payload["linked"] is True
    assert payload["expired"] is False
    assert payload["expires"] is not None


async def test_data_reports_an_expired_link(monkeypatch: pytest.MonkeyPatch) -> None:
    # refresh_api_tokens refuses once the stored expiry passes, so the page must call
    # that state out rather than showing a green "linked".
    expires = dt.datetime.now() - dt.timedelta(days=1)
    _stub_credentials(monkeypatch, _Credentials("a-refresh-token", expires))
    payload = json.loads(_text(await bungie_account._handle_data(_as_request())))
    assert payload["linked"] is True
    assert payload["expired"] is True


async def test_data_reports_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    # A fresh deploy has no row at all — get_credentials returns None.
    _stub_credentials(monkeypatch, None)
    payload = json.loads(_text(await bungie_account._handle_data(_as_request())))
    assert payload == {"linked": False, "expires": None, "expired": False}


# --- login ---------------------------------------------------------------------------


async def test_login_redirects_to_bungie(monkeypatch: pytest.MonkeyPatch) -> None:
    # A redirect, not JSON: the browser has to actually travel to Bungie's consent
    # screen and come back through /oauth/callback.
    monkeypatch.setattr(
        bungie_account, "oauth_url", lambda: "https://www.bungie.net/en/OAuth/Authorize"
    )
    with pytest.raises(aiohttp.web.HTTPFound) as excinfo:
        await bungie_account._handle_login(_as_request())
    # HTTPFound.location is typed str | URL (aiohttp's StrOrURL), so stringify before
    # matching rather than assuming which half it is.
    assert "bungie.net" in str(excinfo.value.location)


async def test_login_url_is_minted_per_click(monkeypatch: pytest.MonkeyPatch) -> None:
    # oauth_url stores a one-shot state code per call, so it must be called when the
    # operator clicks — not once per page render, which would litter unused codes.
    calls = 0

    def _url() -> str:
        nonlocal calls
        calls += 1
        return "https://www.bungie.net/en/OAuth/Authorize"

    monkeypatch.setattr(bungie_account, "oauth_url", _url)
    _stub_credentials(monkeypatch, None)

    await bungie_account._handle_page(_as_request())
    await bungie_account._handle_data(_as_request())
    assert calls == 0, "rendering the page must not mint a state code"

    with pytest.raises(aiohttp.web.HTTPFound):
        await bungie_account._handle_login(_as_request())
    assert calls == 1


# --- account numbers ------------------------------------------------------------------


async def test_account_returns_ids_and_never_the_access_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "super-secret-access-token"

    async def _refresh(*_args: object, **_kwargs: object) -> str:
        return secret

    class _Membership:
        membership_id = 4611686018467260757
        membership_type = 3

        async def get_character_id(self, *_args: object) -> int:
            return 2305843009299797874

    async def _from_api(*_args: object, **_kwargs: object) -> _Membership:
        return _Membership()

    monkeypatch.setattr(bungie_account, "_refresh_api_tokens", _refresh)
    monkeypatch.setattr(bungie_account, "get_webserver_runner", lambda: None)
    monkeypatch.setattr(bungie_account.DestinyMembership, "from_api", _from_api)

    body = _text(await bungie_account._handle_account(_as_request()))
    payload = json.loads(body)
    assert payload["characterId"] == "2305843009299797874"
    assert payload["membershipId"] == "4611686018467260757"
    assert payload["membershipType"] == "3"
    # The whole reason the Discord command carried a note about this.
    assert secret not in body


async def test_account_reports_a_failure_as_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An expired link raises from refresh_api_tokens; the page shows the message rather
    # than 500ing, so "log in again" is the obvious next step.
    async def _refresh(*_args: object, **_kwargs: object) -> str:
        raise ValueError("Bungie credentials have expired, please log in again")

    monkeypatch.setattr(bungie_account, "_refresh_api_tokens", _refresh)
    monkeypatch.setattr(bungie_account, "get_webserver_runner", lambda: None)

    payload = json.loads(_text(await bungie_account._handle_account(_as_request())))
    assert "expired" in payload["error"]


# --- wiring ---------------------------------------------------------------------------


async def test_routes_and_card_registered() -> None:
    app = aiohttp.web.Application()
    bungie_account.register_bungie_account_routes(app)
    paths = {getattr(r.resource, "canonical", None) for r in app.router.routes()}
    assert {"/bungie", "/bungie/data", "/bungie/login", "/bungie/account"} <= paths
