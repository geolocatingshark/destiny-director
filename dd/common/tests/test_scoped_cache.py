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

"""Tests for the beacon channel-scoped gateway cache."""

import base64
import types
import typing as t
from unittest.mock import MagicMock, patch

import hikari as h
from hikari.impl.cache import CacheImpl

from dd.common.bot import ServerEmojiEnabledBot
from dd.common.scoped_cache import ScopedChannelCacheImpl


def _fake_token() -> str:
    """A structurally-valid bot token so GatewayBot can parse its app id offline."""
    app_id = b"123456789012345678"
    return base64.b64encode(app_id).decode().rstrip("=") + ".Xxxxxx." + "y" * 27


def _fake_channel(channel_id: int) -> h.PermissibleGuildChannel:
    """A minimal stand-in carrying just the ``id`` the gate reads."""
    return t.cast(
        h.PermissibleGuildChannel,
        types.SimpleNamespace(id=channel_id, guild_id=1),
    )


def _text_channel_payload(channel_id: int, guild_id: int = 222) -> dict:
    return {
        "id": str(channel_id),
        "type": 0,
        "guild_id": str(guild_id),
        "position": 0,
        "permission_overwrites": [],
        "name": "general",
        "topic": None,
        "nsfw": False,
        "last_message_id": None,
        "rate_limit_per_user": 0,
        "parent_id": None,
        "default_auto_archive_duration": 1440,
        "last_pin_timestamp": None,
    }


def test_gate_refuses_non_relevant_and_admits_relevant():
    """set_guild_channel delegates to super only for ids in the relevant set."""
    relevant = {111}
    cache = ScopedChannelCacheImpl(MagicMock(), h.impl.CacheSettings(), relevant)

    with patch.object(CacheImpl, "set_guild_channel") as super_set:
        cache.set_guild_channel(_fake_channel(999))
        super_set.assert_not_called()  # not relevant -> refused

        cache.set_guild_channel(_fake_channel(111))
        super_set.assert_called_once()  # relevant -> cached


def test_gate_reflects_live_set_mutation():
    """The relevant set is read live, so mutating it changes what gets cached."""
    relevant: set[int] = set()
    cache = ScopedChannelCacheImpl(MagicMock(), h.impl.CacheSettings(), relevant)

    with patch.object(CacheImpl, "set_guild_channel") as super_set:
        cache.set_guild_channel(_fake_channel(555))
        super_set.assert_not_called()

        relevant.add(555)  # mutate the shared set in place
        cache.set_guild_channel(_fake_channel(555))
        super_set.assert_called_once()


def _scoped_bot(relevant: set[int]) -> ServerEmojiEnabledBot:
    return ServerEmojiEnabledBot(
        token=_fake_token(),
        intents=h.Intents.ALL_UNPRIVILEGED,
        cache_settings=h.impl.CacheSettings(
            components=(
                h.api.CacheComponents.GUILDS
                | h.api.CacheComponents.GUILD_CHANNELS
                | h.api.CacheComponents.ROLES
                | h.api.CacheComponents.MEMBERS
                | h.api.CacheComponents.MESSAGES
                | h.api.CacheComponents.ME
            ),
            only_my_member=True,
        ),
        scoped_channel_ids=relevant,
    )


def test_bot_wires_scoped_cache_into_event_manager_and_rest():
    """The scoped cache must replace the cache the event manager + REST hold."""
    bot = _scoped_bot(set())

    assert isinstance(bot.cache, ScopedChannelCacheImpl)
    assert bot._event_manager._cache is bot.cache
    assert bot._rest._cache is bot.cache
    assert bot.cache.settings.only_my_member is True


def test_scoped_cache_end_to_end_set_get():
    """Through a real bot cache: a relevant channel is stored, a non-relevant isn't."""
    bot = _scoped_bot({111})
    cache = t.cast(ScopedChannelCacheImpl, bot.cache)

    def _channel(cid: int) -> h.PermissibleGuildChannel:
        return t.cast(
            h.PermissibleGuildChannel,
            bot.entity_factory.deserialize_channel(_text_channel_payload(cid)),
        )

    cache.set_guild_channel(_channel(111))
    cache.set_guild_channel(_channel(999))

    assert cache.get_guild_channel(111) is not None
    assert cache.get_guild_channel(999) is None
