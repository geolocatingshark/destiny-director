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

# Unit tests for utils.follow_link_single_step. The network is faked (no real HTTP)
# and asyncio.sleep is stubbed, so the retry/fallback behaviour is asserted without
# ever waiting or reaching out.

import typing as t

import pytest

from dd.common import utils

pytestmark = pytest.mark.asyncio


class _FakeResp:
    """Stand-in for an aiohttp response used as ``async with session.get(...)``."""

    def __init__(self, status: int = 200, location: str | None = None) -> None:
        self.status = status
        self.headers: dict[str, str] = (
            {} if location is None else {"Location": location}
        )

    async def __aenter__(self) -> "_FakeResp":
        return self

    async def __aexit__(self, *exc: t.Any) -> bool:
        return False


class _RaisingCtx:
    """A ``session.get(...)`` context manager whose entry raises (network error)."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def __aenter__(self) -> t.NoReturn:
        raise self._exc

    async def __aexit__(self, *exc: t.Any) -> bool:
        return False


class _FakeSession:
    """Fake ClientSession returning a canned result (or exception) per ``get``."""

    def __init__(self, result: _FakeResp | BaseException) -> None:
        self._result = result
        self.get_calls = 0

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: t.Any) -> bool:
        return False

    def get(self, url: str, allow_redirects: bool = False) -> "_FakeResp | _RaisingCtx":
        self.get_calls += 1
        if isinstance(self._result, BaseException):
            return _RaisingCtx(self._result)
        return self._result


def _install(monkeypatch: pytest.MonkeyPatch, session: _FakeSession) -> list[float]:
    """Point utils at ``session`` and record any (stubbed) sleeps."""
    monkeypatch.setattr(utils.aiohttp, "ClientSession", lambda *a, **k: session)
    sleeps: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(utils.aio, "sleep", _fake_sleep)
    return sleeps


async def test_returns_redirect_location(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession(_FakeResp(status=302, location="https://final.example/x"))
    sleeps = _install(monkeypatch, session)

    assert (
        await utils.follow_link_single_step("https://short.example/x")
        == "https://final.example/x"
    )
    assert session.get_calls == 1  # resolved on the first hit, no retries
    assert sleeps == []


async def test_dead_link_falls_back_without_retry_storm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://dead.example/gone"
    session = _FakeSession(_FakeResp(status=404))
    sleeps = _install(monkeypatch, session)

    # A permanent 4xx returns the original url immediately — no sleeping, one request.
    assert await utils.follow_link_single_step(url) == url
    assert session.get_calls == 1
    assert sleeps == []


async def test_timeout_is_swallowed_and_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://hung.example/slow"
    session = _FakeSession(TimeoutError("timed out"))
    sleeps = _install(monkeypatch, session)

    # A hung host must not raise; it retries the bounded number of times then gives up.
    assert await utils.follow_link_single_step(url) == url
    assert session.get_calls == utils._LINK_FOLLOW_RETRIES + 1
    assert len(sleeps) == utils._LINK_FOLLOW_RETRIES


async def test_server_error_retries_then_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://flaky.example/x"
    session = _FakeSession(_FakeResp(status=503))
    sleeps = _install(monkeypatch, session)

    # A 5xx is transient: retry the bounded number of times, then fall back to the url.
    assert await utils.follow_link_single_step(url) == url
    assert session.get_calls == utils._LINK_FOLLOW_RETRIES + 1
    assert len(sleeps) == utils._LINK_FOLLOW_RETRIES
