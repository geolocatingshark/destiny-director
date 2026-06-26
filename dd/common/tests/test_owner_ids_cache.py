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

"""Unit tests for owner-id caching on ``CachedFetchBot`` and the ``/help`` fallback.

``fetch_owner_ids`` used to hit ``rest.fetch_application`` on every call, so the owner
check on hot paths (notably ``/help`` and its autocomplete) could blow Discord's 3s
interaction-ack window. The list is now cached for the process lifetime and warmed at
startup; ``/help``'s admin check additionally degrades to non-admin on a REST failure.
"""

import typing as t
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import hikari as h
import pytest

from dd.common.bot import CachedFetchBot
from dd.common.help import _invoker_is_admin

pytestmark = pytest.mark.asyncio


def _single_owner_app(owner_id: int = 111) -> SimpleNamespace:
    return SimpleNamespace(team=None, owner=SimpleNamespace(id=h.Snowflake(owner_id)))


def _team_app(*member_ids: int) -> SimpleNamespace:
    # fetch_owner_ids only reads ``.team.members.keys()``; values are irrelevant.
    return SimpleNamespace(
        team=SimpleNamespace(members={h.Snowflake(i): object() for i in member_ids}),
        owner=None,
    )


def _bot_with_app(application: object) -> MagicMock:
    """A mock bot whose ``rest.fetch_application`` returns ``application``.

    ``_owner_ids`` is seeded to ``None`` (the "never populated" sentinel) so the real
    ``CachedFetchBot.fetch_owner_ids`` logic runs against it when called unbound.
    """
    bot = MagicMock()
    bot._owner_ids = None
    bot.rest.fetch_application = AsyncMock(return_value=application)
    return bot


# -- fetch_owner_ids caching -------------------------------------------------


async def test_hits_rest_once_then_serves_cache() -> None:
    bot = _bot_with_app(_single_owner_app(111))

    first = await CachedFetchBot.fetch_owner_ids(t.cast(CachedFetchBot, bot))
    second = await CachedFetchBot.fetch_owner_ids(t.cast(CachedFetchBot, bot))

    assert first == [h.Snowflake(111)]
    assert second == first
    bot.rest.fetch_application.assert_awaited_once()  # second call hit the cache


async def test_team_application_shape() -> None:
    bot = _bot_with_app(_team_app(1, 2))
    assert await CachedFetchBot.fetch_owner_ids(t.cast(CachedFetchBot, bot)) == [
        h.Snowflake(1),
        h.Snowflake(2),
    ]


async def test_single_owner_application_shape() -> None:
    bot = _bot_with_app(_single_owner_app(111))
    assert await CachedFetchBot.fetch_owner_ids(t.cast(CachedFetchBot, bot)) == [
        h.Snowflake(111)
    ]


async def test_force_refresh_refetches() -> None:
    bot = _bot_with_app(_single_owner_app(111))

    assert await CachedFetchBot.fetch_owner_ids(t.cast(CachedFetchBot, bot)) == [
        h.Snowflake(111)
    ]
    # Owners changed upstream; force_refresh must re-read rather than serve the cache.
    bot.rest.fetch_application.return_value = _single_owner_app(222)
    refreshed = await CachedFetchBot.fetch_owner_ids(
        t.cast(CachedFetchBot, bot), force_refresh=True
    )

    assert refreshed == [h.Snowflake(222)]
    assert bot.rest.fetch_application.await_count == 2


# -- startup warm + /help fallback -------------------------------------------


async def test_startup_warm_delegates_to_fetch_owner_ids() -> None:
    bot = MagicMock()
    bot.fetch_owner_ids = AsyncMock(return_value=[h.Snowflake(1)])

    await CachedFetchBot._warm_owner_ids_on_start(
        t.cast(CachedFetchBot, bot), MagicMock(spec=h.StartedEvent)
    )

    # Warming just populates the cache via the (now cached) fetch — one fetch, no args.
    bot.fetch_owner_ids.assert_awaited_once_with()


async def test_invoker_is_admin_true_only_for_owners() -> None:
    bot = MagicMock()
    bot.fetch_owner_ids = AsyncMock(return_value=[h.Snowflake(7)])

    assert await _invoker_is_admin(t.cast(CachedFetchBot, bot), h.Snowflake(7)) is True
    assert await _invoker_is_admin(t.cast(CachedFetchBot, bot), h.Snowflake(8)) is False


async def test_invoker_is_admin_falls_back_to_false_on_rest_error() -> None:
    bot = MagicMock()
    bot.fetch_owner_ids = AsyncMock(side_effect=RuntimeError("discord down"))

    # A genuine REST failure must degrade to the non-admin view, not raise.
    assert await _invoker_is_admin(t.cast(CachedFetchBot, bot), h.Snowflake(7)) is False
