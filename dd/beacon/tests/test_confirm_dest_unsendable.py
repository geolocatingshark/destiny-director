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

"""Tests for ``utils.confirm_dest_unsendable`` — the perm-check that gates the mirror
auto-disable (mocked hikari app, no DB / network).

``calculate_permissions`` is patched so these only exercise the verdict wiring: the
cache→REST channel/member fallbacks, thread→parent resolution, and — critically — that
every ambiguity maps to ``UNKNOWN`` while only genuine dead-channel signals map to
``CONFIRMED_*`` (biasing hard toward never disabling a healthy destination)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import hikari as h
import pytest
from toolbox.errors import CacheFailureError

from dd.beacon import utils
from dd.beacon.utils import DestVerdict

pytestmark = pytest.mark.asyncio

_ME = SimpleNamespace(id=999)
_CHANNEL_ID = 456


def _make_app(
    *,
    me: object | None = _ME,
    cached_channel: object | None = None,
    rest_channels: list[object] | None = None,
    cached_member: object | None = object(),
    fetched_member: object | None = None,
) -> MagicMock:
    """A mocked gateway bot. ``cached_channel`` is returned by every
    ``cache.get_guild_channel``; on a miss (``None``) ``rest.fetch_channel`` is consumed
    in order from ``rest_channels`` (so a thread test hands back thread then parent)."""
    app = MagicMock()
    app.get_me = MagicMock(return_value=me)
    app.cache.get_guild_channel = MagicMock(return_value=cached_channel)
    app.cache.get_member = MagicMock(return_value=cached_member)
    app.rest.fetch_channel = AsyncMock(side_effect=list(rest_channels or []))
    app.rest.fetch_member = AsyncMock(return_value=fetched_member)
    return app


def _text_channel(guild_id: int = 123) -> MagicMock:
    ch = MagicMock(spec=h.GuildTextChannel)
    ch.guild_id = guild_id
    return ch


def _thread(parent_id: int = 789) -> MagicMock:
    thread = MagicMock(spec=h.GuildThreadChannel)
    thread.parent_id = parent_id
    return thread


def _forbidden() -> h.ForbiddenError:
    return h.ForbiddenError(url="u", headers={}, raw_body=b"")


def _not_found() -> h.NotFoundError:
    return h.NotFoundError(url="u", headers={}, raw_body=b"")


async def test_sendable_when_view_and_send_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app(cached_channel=_text_channel())
    monkeypatch.setattr(
        utils,
        "calculate_permissions",
        lambda _m, _c: h.Permissions.VIEW_CHANNEL | h.Permissions.SEND_MESSAGES,
    )
    assert await utils.confirm_dest_unsendable(app, _CHANNEL_ID) is DestVerdict.SENDABLE


async def test_confirmed_unsendable_when_send_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app(cached_channel=_text_channel())
    monkeypatch.setattr(
        utils, "calculate_permissions", lambda _m, _c: h.Permissions.VIEW_CHANNEL
    )
    assert (
        await utils.confirm_dest_unsendable(app, _CHANNEL_ID)
        is DestVerdict.CONFIRMED_UNSENDABLE
    )


async def test_confirmed_gone_on_channel_not_found() -> None:
    # Cache miss then a 404 on fetch → the channel is gone.
    app = _make_app(cached_channel=None, rest_channels=[_not_found()])
    assert (
        await utils.confirm_dest_unsendable(app, _CHANNEL_ID)
        is DestVerdict.CONFIRMED_GONE
    )


async def test_confirmed_gone_on_channel_forbidden() -> None:
    # A 403 on fetch → the bot can't even see the channel.
    app = _make_app(cached_channel=None, rest_channels=[_forbidden()])
    assert (
        await utils.confirm_dest_unsendable(app, _CHANNEL_ID)
        is DestVerdict.CONFIRMED_GONE
    )


async def test_confirmed_gone_when_bot_not_in_guild() -> None:
    # Channel resolves, but the bot's member fetch 404s → it was kicked from the guild.
    app = _make_app(
        cached_channel=_text_channel(),
        cached_member=None,
        fetched_member=None,
    )
    app.rest.fetch_member = AsyncMock(side_effect=_not_found())
    assert (
        await utils.confirm_dest_unsendable(app, _CHANNEL_ID)
        is DestVerdict.CONFIRMED_GONE
    )


async def test_unknown_when_bot_user_unknown() -> None:
    app = _make_app(me=None)
    assert await utils.confirm_dest_unsendable(app, _CHANNEL_ID) is DestVerdict.UNKNOWN


async def test_unknown_on_cache_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_m: object, _c: object) -> h.Permissions:
        raise CacheFailureError("uncacheable")

    app = _make_app(cached_channel=_text_channel())
    monkeypatch.setattr(utils, "calculate_permissions", boom)
    assert await utils.confirm_dest_unsendable(app, _CHANNEL_ID) is DestVerdict.UNKNOWN


async def test_unknown_on_non_permissible_channel() -> None:
    # A non-permissible channel type can't have perms computed → UNKNOWN, not a disable.
    app = _make_app(cached_channel=MagicMock(spec=h.PartialChannel))
    assert await utils.confirm_dest_unsendable(app, _CHANNEL_ID) is DestVerdict.UNKNOWN


async def test_thread_resolves_to_parent_for_perms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[object] = []

    def fake_calc(_m: object, channel: object) -> h.Permissions:
        seen.append(channel)
        # A thread needs the in-thread send perm, not the base send perm, to be OK.
        return h.Permissions.VIEW_CHANNEL | h.Permissions.SEND_MESSAGES_IN_THREADS

    thread = _thread(parent_id=789)
    parent = _text_channel()
    # Cache misses for both ids → REST returns the thread, then its parent.
    app = _make_app(cached_channel=None, rest_channels=[thread, parent])
    monkeypatch.setattr(utils, "calculate_permissions", fake_calc)

    assert await utils.confirm_dest_unsendable(app, _CHANNEL_ID) is DestVerdict.SENDABLE
    app.rest.fetch_channel.assert_any_await(789)
    assert seen == [parent]  # perms computed against the parent, not the thread


async def test_thread_needs_in_thread_send_perm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Holding only the base Send Messages perm (but not Send Messages In Threads) on a
    # thread's parent is NOT enough to post in the thread → CONFIRMED_UNSENDABLE.
    def fake_calc(_m: object, _c: object) -> h.Permissions:
        return h.Permissions.VIEW_CHANNEL | h.Permissions.SEND_MESSAGES

    thread = _thread(parent_id=789)
    parent = _text_channel()
    app = _make_app(cached_channel=None, rest_channels=[thread, parent])
    monkeypatch.setattr(utils, "calculate_permissions", fake_calc)

    assert (
        await utils.confirm_dest_unsendable(app, _CHANNEL_ID)
        is DestVerdict.CONFIRMED_UNSENDABLE
    )
