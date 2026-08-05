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

# Per-feed actions: preview returns the node tree the shared renderer draws (and reports
# a build failure as data rather than a 500), and send guards its preconditions —
# dormant feed, no announcer, one already in flight — and never posts when the build
# fails. The buttons that call these live on /autopost_settings; see
# test_autopost_settings.py. Exercised with fake requests (no live server); auth is the
# web_auth middleware, covered in test_web_auth.py.

import asyncio
import json
import typing as t

import aiohttp.web
import hikari as h
import pytest

from dd.anchor import autopost
from dd.anchor.extensions import feed_actions
from dd.hmessage import HMessage

pytestmark = pytest.mark.asyncio


class _FakeRequest:
    """Minimal aiohttp.web.Request stand-in: path vars plus an awaitable ``.json()``."""

    def __init__(self, name: str, payload: object | None = None) -> None:
        self.match_info = {"name": name}
        self._payload = payload

    async def json(self) -> object:
        if self._payload is None:
            raise ValueError("no body")
        return self._payload


def _as_request(req: _FakeRequest) -> aiohttp.web.Request:
    return t.cast(aiohttp.web.Request, req)


def _text(resp: aiohttp.web.Response) -> str:
    """Response.text is typed ``str | None``; every handler here sets it."""
    assert resp.text is not None
    return resp.text


def _post() -> HMessage:
    """A minimal CV2 post — one container holding one text display."""
    container = h.impl.ContainerComponentBuilder()
    container.add_text_display("Hello from the feed")
    return HMessage(components=[container])


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch: pytest.MonkeyPatch) -> t.Iterator[None]:
    """Swap the import-time feed registry for a controlled one, and stub the bot.

    The real registry is populated by the producer modules at import; tests need
    predictable feeds (including a dormant one) without touching them.
    """
    monkeypatch.setattr(autopost, "_feeds", {})
    monkeypatch.setattr(feed_actions, "_bot", object())
    monkeypatch.setattr(feed_actions, "_sending", set())
    yield


async def _constructor(**_kwargs: object) -> HMessage:
    return _post()


async def _failing_constructor(**_kwargs: object) -> HMessage:
    raise RuntimeError("no event scheduled")


def _register(
    name: str = "xur",
    *,
    channel_id: int | None = 123,
    constructor: t.Callable[..., t.Awaitable[HMessage]] | None = None,
    announcer: t.Callable[..., t.Awaitable[t.Any]] | None = None,
) -> None:
    autopost.register_feed(
        autopost.Feed(
            name=name,
            channel_id=channel_id,
            message_constructor_coro=constructor or _constructor,
            message_announcer_coro=announcer,
        )
    )


# --- the page shell ----------------------------------------------------------------


async def test_unknown_feed_404s() -> None:
    with pytest.raises(aiohttp.web.HTTPNotFound):
        await feed_actions._handle_preview(_as_request(_FakeRequest("nope")))


# --- preview -----------------------------------------------------------------------


async def test_preview_returns_the_node_tree_for_the_shared_renderer() -> None:
    # The same {kind, payload, message_kind} shape /mirror-logs/render serves, so the
    # page draws it with the identical CV2Render.snapshotSpec call.
    _register()
    res = await feed_actions._handle_preview(_as_request(_FakeRequest("xur")))
    payload = json.loads(_text(res))
    assert payload["kind"] == "snapshot"
    assert payload["message_kind"] == "cv2"
    assert "Hello from the feed" in json.dumps(payload["payload"])


async def test_preview_reports_a_build_failure_as_data() -> None:
    # Iron Banner between events raises; the Discord `show` reported that inline, so the
    # page must render it in the preview box rather than 500.
    _register(constructor=_failing_constructor)
    res = await feed_actions._handle_preview(_as_request(_FakeRequest("xur")))
    assert "no event scheduled" in json.loads(_text(res))["error"]


async def test_preview_works_while_dormant() -> None:
    # Construction needs no channel, so a dormant feed still previews.
    _register(channel_id=None)
    res = await feed_actions._handle_preview(_as_request(_FakeRequest("xur")))
    assert "Hello from the feed" in json.dumps(json.loads(_text(res))["payload"])


# --- send --------------------------------------------------------------------------


async def test_send_starts_the_announcer_and_returns() -> None:
    seen: dict[str, t.Any] = {}
    started = asyncio.Event()

    async def _announcer(**kwargs: t.Any) -> None:
        seen.update(kwargs)
        started.set()

    _register(announcer=_announcer)
    res = await feed_actions._handle_send(_as_request(_FakeRequest("xur", {})))
    assert json.loads(_text(res)) == {"ok": True, "started": True}

    await asyncio.wait_for(started.wait(), timeout=1)
    assert seen["channel_id"] == 123
    # A manual send always posts, whatever the autopost toggle says.
    assert seen["check_enabled"] is False
    assert seen["publish_message"] is True


async def test_send_honours_publish_false() -> None:
    seen: dict[str, t.Any] = {}
    started = asyncio.Event()

    async def _announcer(**kwargs: t.Any) -> None:
        seen.update(kwargs)
        started.set()

    _register(announcer=_announcer)
    await feed_actions._handle_send(
        _as_request(_FakeRequest("xur", {"publish": False}))
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    assert seen["publish_message"] is False


async def test_send_refuses_a_dormant_feed() -> None:
    called = False

    async def _announcer(**_kwargs: t.Any) -> None:
        nonlocal called
        called = True

    _register(channel_id=None, announcer=_announcer)
    res = await feed_actions._handle_send(_as_request(_FakeRequest("xur", {})))
    assert res.status == 409
    assert "dormant" in json.loads(_text(res))["error"]
    assert not called


async def test_send_refuses_without_an_announcer() -> None:
    _register(announcer=None)
    res = await feed_actions._handle_send(_as_request(_FakeRequest("xur", {})))
    assert res.status == 409


async def test_send_does_not_post_when_the_build_fails() -> None:
    # The pre-flight build is the whole point of building before spawning: a broken
    # constructor must not reach the announcer, which would post a placeholder first.
    called = False

    async def _announcer(**_kwargs: t.Any) -> None:
        nonlocal called
        called = True

    _register(constructor=_failing_constructor, announcer=_announcer)
    res = await feed_actions._handle_send(_as_request(_FakeRequest("xur", {})))
    assert res.status == 502
    assert "nothing was sent" in json.loads(_text(res))["error"]
    assert not called


async def test_send_rejects_a_second_send_while_one_is_in_flight() -> None:
    release = asyncio.Event()

    async def _announcer(**_kwargs: t.Any) -> None:
        await release.wait()

    _register(announcer=_announcer)
    first = await feed_actions._handle_send(_as_request(_FakeRequest("xur", {})))
    assert first.status == 200

    second = await feed_actions._handle_send(_as_request(_FakeRequest("xur", {})))
    assert second.status == 409
    assert "already in flight" in json.loads(_text(second))["error"]

    # Once the first finishes, the slot is released and a send is allowed again.
    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert "xur" not in feed_actions._sending


async def test_routes_registered_without_a_page_or_card() -> None:
    # Actions only: no page shell and no control-panel card. The buttons live on the
    # /autopost_settings rows, so a per-feed page (or a card per feed — exactly the flat
    # list that was rejected) would be a click in the way.
    app = aiohttp.web.Application()
    feed_actions.register_feed_action_routes(app)
    paths = {getattr(r.resource, "canonical", None) for r in app.router.routes()}
    assert "/feed/{name}/preview" in paths
    assert "/feed/{name}/send" in paths
