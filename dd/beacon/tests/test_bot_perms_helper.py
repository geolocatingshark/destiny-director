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

"""Plumbing tests for ``utils.compute_bot_perms`` (mocked hikari app, no DB / network).

``calculate_permissions`` is the toolbox routine that does the actual math; here it's
patched to a sentinel so these tests only exercise the resolution wiring (guild/bot-user
guards, thread→parent, cache→REST member fallback, and the ``None`` escapes)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import hikari as h
import pytest
from toolbox.errors import CacheFailureError

from dd.beacon import utils

pytestmark = pytest.mark.asyncio

_ME = SimpleNamespace(id=999)


def _make_ctx(
    *,
    guild_id: int | None = 123,
    channel_id: int = 456,
    me: object | None = _ME,
    cached_member: object | None = None,
    fetched_member: object | None = None,
    channels: list[object] | None = None,
) -> MagicMock:
    """A stub ``lb.Context`` whose ``client.app`` is a mocked gateway bot.

    ``channels`` is consumed in order by successive ``rest.fetch_channel`` calls (so a
    thread test can hand back ``[thread, parent]``)."""
    app = MagicMock()
    app.get_me = MagicMock(return_value=me)
    app.cache.get_member = MagicMock(return_value=cached_member)
    app.rest.fetch_member = AsyncMock(return_value=fetched_member)
    app.rest.fetch_channel = AsyncMock(side_effect=list(channels or []))

    ctx = MagicMock()
    ctx.client.app = app
    ctx.guild_id = guild_id
    ctx.channel_id = channel_id
    return ctx


def _text_channel() -> MagicMock:
    return MagicMock(spec=h.GuildTextChannel)


def _thread(parent_id: int = 789) -> MagicMock:
    thread = MagicMock(spec=h.GuildThreadChannel)
    thread.parent_id = parent_id
    return thread


async def test_no_guild_returns_none() -> None:
    assert await utils.compute_bot_perms(_make_ctx(guild_id=None)) is None


async def test_unknown_bot_user_returns_none() -> None:
    assert await utils.compute_bot_perms(_make_ctx(me=None)) is None


async def test_computes_perms_for_text_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _make_ctx(cached_member=object(), channels=[_text_channel()])
    monkeypatch.setattr(
        utils, "calculate_permissions", lambda _m, _c: h.Permissions.SEND_MESSAGES
    )
    assert await utils.compute_bot_perms(ctx) == h.Permissions.SEND_MESSAGES


async def test_thread_resolves_to_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []

    def fake_calc(_m: object, channel: object) -> h.Permissions:
        seen.append(channel)
        return h.Permissions.VIEW_CHANNEL

    thread = _thread(parent_id=789)
    parent = _text_channel()
    ctx = _make_ctx(cached_member=object(), channels=[thread, parent])
    monkeypatch.setattr(utils, "calculate_permissions", fake_calc)

    assert await utils.compute_bot_perms(ctx) == h.Permissions.VIEW_CHANNEL
    # Fetched the target then its parent, and computed perms against the parent.
    assert ctx.client.app.rest.fetch_channel.await_count == 2
    ctx.client.app.rest.fetch_channel.assert_any_await(789)
    assert seen == [parent]


async def test_cache_miss_falls_back_to_rest_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched = object()
    seen: list[object] = []

    def fake_calc(member: object, _c: object) -> h.Permissions:
        seen.append(member)
        return h.Permissions.SEND_MESSAGES

    ctx = _make_ctx(
        cached_member=None, fetched_member=fetched, channels=[_text_channel()]
    )
    monkeypatch.setattr(utils, "calculate_permissions", fake_calc)

    assert await utils.compute_bot_perms(ctx) == h.Permissions.SEND_MESSAGES
    ctx.client.app.rest.fetch_member.assert_awaited_once()
    assert seen == [fetched]


async def test_cache_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_m: object, _c: object) -> h.Permissions:
        raise CacheFailureError("uncacheable")

    ctx = _make_ctx(cached_member=object(), channels=[_text_channel()])
    monkeypatch.setattr(utils, "calculate_permissions", boom)
    assert await utils.compute_bot_perms(ctx) is None


async def test_non_permissible_channel_returns_none() -> None:
    # A non-permissible target can't have perms computed → None (and the perm math is
    # never reached, so it's left unpatched here).
    ctx = _make_ctx(cached_member=object(), channels=[MagicMock(spec=h.PartialChannel)])
    assert await utils.compute_bot_perms(ctx) is None


async def test_forbidden_channel_fetch_returns_none() -> None:
    # No View Channel → the REST channel fetch 403s; this must resolve to None rather
    # than propagate (which used to crash the command before any diagnostic rendered).
    forbidden = h.ForbiddenError(url="u", headers={}, raw_body=b"")
    ctx = _make_ctx(cached_member=object(), channels=[forbidden])
    assert await utils.compute_bot_perms(ctx) is None
