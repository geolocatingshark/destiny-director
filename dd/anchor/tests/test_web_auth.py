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

"""Discord-OAuth web auth: session cookie primitive, enforcement middleware and the
OAuth login/callback/logout routes, all against fake requests and a mocked outbound
``aiohttp.ClientSession`` (no live server, no network — CI-safe)."""

import datetime as dt
import typing as t

import aiohttp.web
import pytest
from yarl import URL

from dd.anchor.extensions import web_auth as auth

pytestmark = pytest.mark.asyncio


# --- fake request / handler harness -----------------------------------------------


class _FakeRequest:
    """Minimal aiohttp.web.Request stand-in for the auth handlers + middleware."""

    def __init__(
        self,
        *,
        path: str = "/",
        method: str = "GET",
        query: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        path_qs: str | None = None,
    ) -> None:
        self.path = path
        self.method = method
        self.query = query or {}
        self.cookies = cookies or {}
        self.headers = headers or {}
        self.path_qs = path_qs if path_qs is not None else path


def _req(**kwargs: t.Any) -> aiohttp.web.Request:
    return t.cast(aiohttp.web.Request, _FakeRequest(**kwargs))


async def _ok_handler(request: aiohttp.web.Request) -> aiohttp.web.Response:
    """A downstream handler that only runs if the middleware admitted the request."""
    return aiohttp.web.Response(text="OK")


class _StubBot:
    """A CachedFetchBot stand-in whose owner list is a fixed set of ids."""

    def __init__(self, owner_ids: list[int]) -> None:
        self._owner_ids = owner_ids

    async def fetch_owner_ids(self) -> list[int]:
        return self._owner_ids


def _owner_cookie(user_id: int = 123) -> dict[str, str]:
    return {auth._SESSION_COOKIE: auth.mint_session(user_id)}


# --- fake ClientSession (OAuth token + user endpoints) ----------------------------


class _FakeResp:
    def __init__(self, data: t.Any) -> None:
        self._data = data

    async def __aenter__(self) -> "_FakeResp":
        return self

    async def __aexit__(self, *exc: t.Any) -> bool:
        return False

    async def json(self) -> t.Any:
        return self._data


class _FakeClientSession:
    """Returns canned JSON for the token/user endpoints; configured via class attrs."""

    token_json: t.Any = {"access_token": "tok"}
    user_json: t.Any = {"id": "123"}

    def __init__(self, *args: t.Any, **kwargs: t.Any) -> None:
        pass

    async def __aenter__(self) -> "_FakeClientSession":
        return self

    async def __aexit__(self, *exc: t.Any) -> bool:
        return False

    def post(self, url: str, **kwargs: t.Any) -> _FakeResp:
        return _FakeResp(type(self).token_json)

    def get(self, url: str, **kwargs: t.Any) -> _FakeResp:
        return _FakeResp(type(self).user_json)


def _patch_oauth_http(
    monkeypatch: pytest.MonkeyPatch,
    *,
    token_json: t.Any = None,
    user_json: t.Any = None,
) -> None:
    token_json = {"access_token": "tok"} if token_json is None else token_json
    user_json = {"id": "123"} if user_json is None else user_json
    session_cls = type(
        "_CfgSession",
        (_FakeClientSession,),
        {"token_json": token_json, "user_json": user_json},
    )
    monkeypatch.setattr(auth.aiohttp, "ClientSession", session_cls)


@pytest.fixture
def oauth_cfg(monkeypatch: pytest.MonkeyPatch):
    """Configure a public base URL + OAuth client creds for the flow tests."""
    monkeypatch.setattr(auth.cfg, "public_base_url", "https://anchor.example")
    monkeypatch.setattr(auth.cfg, "discord_oauth_client_id", "123456")
    monkeypatch.setattr(auth.cfg, "discord_oauth_client_secret", "shh")
    # Ensure the dev bypass is inert unless a test opts in.
    monkeypatch.setattr(auth.cfg, "test_env", ())
    monkeypatch.setattr(auth.cfg, "dev_auth_user_id", "")


# --- session cookie primitive -----------------------------------------------------


async def test_session_mint_resolves_to_user_id() -> None:
    token = auth.mint_session(4242)
    assert auth.resolve_session(token) == 4242


async def test_session_rejects_garbage_and_tampering() -> None:
    assert auth.resolve_session("") is None
    assert auth.resolve_session("never-minted") is None
    assert auth.resolve_session("a.b") is None  # wrong arity
    token = auth.mint_session(123)
    user_str, expiry_str, sig = token.split(".")
    # Tampered user id no longer matches the signature.
    assert auth.resolve_session(f"999.{expiry_str}.{sig}") is None
    # Tampered (extended) expiry no longer matches the signature.
    assert auth.resolve_session(f"{user_str}.{int(expiry_str) + 100_000}.{sig}") is None
    # Forged signature.
    assert auth.resolve_session(f"{user_str}.{expiry_str}.deadbeef") is None
    # A non-ASCII cookie must fail closed (compare_digest would raise TypeError).
    assert auth.resolve_session("1.2.\udcff") is None
    assert auth.resolve_session("héllo") is None


async def test_session_expired_token_is_rejected() -> None:
    past = int((dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)).timestamp())
    expired = auth._sign(123, past)
    assert auth.resolve_session(expired) is None


# --- next-path sanitizer (open-redirect guard) ------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/weekly_reset", "/weekly_reset"),
        ("/rotation/edit?type=lost_sector", "/rotation/edit?type=lost_sector"),
        ("", "/"),
        ("//evil.com", "/"),
        ("https://evil.com", "/"),
        ("/\\evil.com", "/"),
        ("/path\\with\\backslash", "/"),
        ("relative", "/"),
        # Control-char smuggling: a browser/aiohttp strips the whitespace control when
        # emitting the Location header, collapsing "/\t/evil.com" -> "//evil.com".
        ("/\t/evil.com", "/"),
        ("/\n/evil.com", "/"),
        ("/\r/evil.com", "/"),
    ],
)
async def test_sanitize_next(raw: str, expected: str) -> None:
    assert auth._sanitize_next(raw) == expected


# --- enforcement middleware -------------------------------------------------------


@pytest.mark.parametrize("path", ["/auth/login", "/oauth/callback", "/static/app.js"])
async def test_middleware_allowlist_passes_without_cookie(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    monkeypatch.setattr(auth, "_bot", _StubBot([123]))
    resp = await auth._auth_middleware(_req(path=path), _ok_handler)
    assert resp.status == 200


async def test_middleware_unauth_get_redirects_to_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "_bot", _StubBot([123]))
    resp = await auth._auth_middleware(
        _req(path="/rotation", path_qs="/rotation"), _ok_handler
    )
    assert resp.status == 302
    assert resp.headers["Location"] == "/auth/login?next=/rotation"


async def test_middleware_unauth_post_is_401_not_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "_bot", _StubBot([123]))
    resp = await auth._auth_middleware(
        _req(path="/rotation/edit", method="POST"), _ok_handler
    )
    assert resp.status == 401


async def test_middleware_owner_cookie_runs_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "_bot", _StubBot([123]))
    resp = await auth._auth_middleware(
        _req(path="/rotation", cookies=_owner_cookie(123)), _ok_handler
    )
    assert resp.status == 200


async def test_middleware_non_owner_cookie_is_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "_bot", _StubBot([123]))
    resp = await auth._auth_middleware(
        _req(path="/rotation", cookies=_owner_cookie(999)), _ok_handler
    )
    assert resp.status == 403


async def test_middleware_origin_mismatch_on_post_is_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "_bot", _StubBot([123]))
    monkeypatch.setattr(auth.cfg, "public_base_url", "https://anchor.example")
    resp = await auth._auth_middleware(
        _req(
            path="/rotation/edit",
            method="POST",
            cookies=_owner_cookie(123),
            headers={"Origin": "https://evil.example"},
        ),
        _ok_handler,
    )
    assert resp.status == 403


async def test_middleware_same_origin_post_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "_bot", _StubBot([123]))
    monkeypatch.setattr(auth.cfg, "public_base_url", "https://anchor.example")
    resp = await auth._auth_middleware(
        _req(
            path="/rotation/edit",
            method="POST",
            cookies=_owner_cookie(123),
            headers={"Origin": "https://anchor.example"},
        ),
        _ok_handler,
    )
    assert resp.status == 200


async def test_middleware_bot_unset_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "_bot", None)
    # A valid owner cookie clears auth resolution, so the bot-None guard is what fires.
    resp = await auth._auth_middleware(
        _req(path="/rotation", cookies=_owner_cookie(123)), _ok_handler
    )
    assert resp.status == 503


async def test_middleware_dev_bypass_double_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "_bot", _StubBot([123]))
    # Both gates on: an unauthenticated request passes as an owner.
    monkeypatch.setattr(auth.cfg, "test_env", (1000,))
    monkeypatch.setattr(auth.cfg, "dev_auth_user_id", "1000")
    resp = await auth._auth_middleware(_req(path="/rotation"), _ok_handler)
    assert resp.status == 200
    # TEST_ENV unset -> bypass inert even with a dev id set; unauth GET redirects.
    monkeypatch.setattr(auth.cfg, "test_env", ())
    resp = await auth._auth_middleware(
        _req(path="/rotation", path_qs="/rotation"), _ok_handler
    )
    assert resp.status == 302


# --- /auth/login ------------------------------------------------------------------


async def test_login_redirects_to_discord_authorize(oauth_cfg) -> None:
    resp = await auth._handle_login(_req(query={"next": "/weekly_reset"}))
    assert resp.status == 302
    location = URL(resp.headers["Location"])
    assert location.host == "discord.com"
    assert "/oauth2/authorize" in location.path
    q = location.query
    assert q["client_id"] == "123456"
    assert q["scope"] == "identify"
    assert q["response_type"] == "code"
    assert q["redirect_uri"] == "https://anchor.example/auth/callback"
    assert q["state"]  # a state code is present


async def test_login_without_base_url_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth.cfg, "public_base_url", "")
    resp = await auth._handle_login(_req())
    assert resp.status == 500


async def test_login_preserves_and_sanitizes_next(oauth_cfg) -> None:
    # A hostile ``next`` is reduced to "/" before it is stored in the state.
    resp = await auth._handle_login(_req(query={"next": "https://evil.com"}))
    state = URL(resp.headers["Location"]).query["state"]
    assert auth._AuthStateManager.consume(state) == "/"
    # A legitimate internal path is preserved.
    resp = await auth._handle_login(_req(query={"next": "/weekly_reset"}))
    state = URL(resp.headers["Location"]).query["state"]
    assert auth._AuthStateManager.consume(state) == "/weekly_reset"


# --- /auth/callback ---------------------------------------------------------------


async def test_callback_happy_path_sets_cookie_and_redirects(
    oauth_cfg, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, "_bot", _StubBot([123]))
    _patch_oauth_http(monkeypatch, user_json={"id": "123"})
    state = auth._AuthStateManager.issue("/weekly_reset")
    resp = await auth._handle_callback(
        _req(query={"state": state, "code": "abc"})
    )
    assert resp.status == 302
    assert resp.headers["Location"] == "/weekly_reset"
    morsel = resp.cookies[auth._SESSION_COOKIE]
    assert auth.resolve_session(morsel.value) == 123
    assert morsel["httponly"]
    assert morsel["samesite"] == "Lax"
    assert morsel["path"] == "/"


async def test_callback_unknown_state_is_400(
    oauth_cfg, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, "_bot", _StubBot([123]))
    _patch_oauth_http(monkeypatch)
    resp = await auth._handle_callback(
        _req(query={"state": "never-issued", "code": "abc"})
    )
    assert resp.status == 400


async def test_callback_reused_state_is_400(
    oauth_cfg, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, "_bot", _StubBot([123]))
    _patch_oauth_http(monkeypatch)
    state = auth._AuthStateManager.issue("/")
    first = await auth._handle_callback(_req(query={"state": state, "code": "abc"}))
    assert first.status == 302
    # Single-use: the same state can't be replayed.
    second = await auth._handle_callback(_req(query={"state": state, "code": "abc"}))
    assert second.status == 400


async def test_callback_non_owner_is_403_without_cookie(
    oauth_cfg, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, "_bot", _StubBot([123]))
    _patch_oauth_http(monkeypatch, user_json={"id": "999"})
    state = auth._AuthStateManager.issue("/")
    resp = await auth._handle_callback(_req(query={"state": state, "code": "abc"}))
    assert resp.status == 403
    assert auth._SESSION_COOKIE not in resp.cookies


async def test_callback_error_param_is_403(oauth_cfg) -> None:
    resp = await auth._handle_callback(_req(query={"error": "access_denied"}))
    assert resp.status == 403


async def test_callback_token_exchange_failure_is_502(
    oauth_cfg, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth, "_bot", _StubBot([123]))
    # Discord returned an error body (no access_token).
    _patch_oauth_http(monkeypatch, token_json={"error": "invalid_grant"})
    state = auth._AuthStateManager.issue("/")
    resp = await auth._handle_callback(_req(query={"state": state, "code": "abc"}))
    assert resp.status == 502


# --- /auth/logout -----------------------------------------------------------------


async def test_logout_clears_cookie_and_redirects() -> None:
    resp = await auth._handle_logout(_req(method="POST"))
    assert resp.status == 302
    assert resp.headers["Location"] == "/auth/login"
    # A deletion cookie is emitted (empty value / expired).
    morsel = resp.cookies[auth._SESSION_COOKIE]
    assert morsel.value == ""
