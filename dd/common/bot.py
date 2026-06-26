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

# Shared discord bot classes used by both the beacon and anchor bots.
# These are h.GatewayBot subclasses with added utility functions. With lightbulb v3
# the command client is a separate object (see each bot's __main__); commands reach
# these helpers through ``ctx.client.app`` and listeners/tasks through dependency
# injection.

import asyncio
import logging
import typing as t

import hikari as h


class CachedFetchBot(h.GatewayBot):
    """h.GatewayBot subclass with async methods that fetch objects from cache if
    possible"""

    def __init__(self, *args: t.Any, **kwargs: t.Any):
        super().__init__(*args, **kwargs)
        self.cache: h.api.MutableCache
        # Number of guilds the bot is in. The anchor bot maintains this via the
        # listeners in dd.anchor.__main__; beacon tracks its count separately
        # through dependency injection (see guild_count_status).
        self.guild_count: int = 0
        # Bot owner ids, cached for the process lifetime to avoid a REST
        # ``fetch_application`` round-trip on every owner check — those sit on
        # latency-sensitive paths (/help and its autocomplete, the owner_only gate,
        # menu buttons) where the round-trip can blow Discord's 3s ack window. Warmed
        # once on StartedEvent so no interaction pays the cold-cache cost; refresh
        # explicitly with ``fetch_owner_ids(force_refresh=True)``.
        self._owner_ids: list[h.Snowflake] | None = None
        _ = self.listen(h.StartedEvent)(self._warm_owner_ids_on_start)

    async def fetch_channel(self, channel_id: int):
        """This method fetches a channel from the cache or from discord if not cached"""
        channel = self.cache.get_guild_channel(channel_id)
        if channel:
            return channel

        channel = await self.rest.fetch_channel(channel_id)
        if isinstance(channel, h.PermissibleGuildChannel):
            # GuildThreadChannels don't seem to be supported by the cache
            t.cast(h.api.MutableCache, self.cache).set_guild_channel(channel)

        return channel

    async def fetch_guild(self, guild_id: int):
        """This method fetches a guild from the cache or from discord if not cached"""
        guild = self.cache.get_guild(guild_id)
        if guild:
            return guild

        guild = await self.rest.fetch_guild(guild_id)
        # Do not put RESTGuilds into the cache since Hikari misbehaves
        # when this is done
        # self.cache.set_guild(guild)

        return guild

    async def fetch_message(
        self, channel: h.SnowflakeishOr[h.TextableChannel], message_id: int
    ):
        """This method fetches a message from the cache or from discord if not cached

        channel can be the channels id or the channel object itself"""
        if isinstance(channel, (h.Snowflake, int)):
            # If a channel id is specified then get the channel for that id
            # I am not sure if the int check is necessary since Snowflakes
            # are subcalsses of int but want to test this later and remove
            # it only after double checking. Most likely can remove, and I'm
            # just being paranoid
            channel = t.cast(h.TextableChannel, await self.fetch_channel(channel))

        message = self.cache.get_message(message_id)
        if message:
            return message

        message = await self.rest.fetch_message(channel, message_id)
        t.cast(h.api.MutableCache, self.cache).set_message(message)

        return message

    async def fetch_emoji(self, guild_id: int, emoji_id: int):
        """This method fetches an emoji from the cache or from discord if not cached"""
        # TODO allow passing a guild (not id) to this method as well for convenience

        emoji = self.cache.get_emoji(emoji_id)
        if emoji:
            return emoji

        emoji = await self.rest.fetch_emoji(guild_id, emoji_id)
        t.cast(h.api.MutableCache, self.cache).set_emoji(emoji)

        return emoji

    async def fetch_user(self, user_id: int):
        """This method fetches a user from the cache or from discord if not cached"""
        return self.cache.get_user(user_id) or await self.rest.fetch_user(user_id)

    async def fetch_owner_ids(
        self, *, force_refresh: bool = False
    ) -> list[h.Snowflake]:
        """Return the bot owner id(s), cached for the process lifetime.

        Replaces lightbulb v2's ``BotApp.fetch_owner_ids``. The first call (or any
        call with ``force_refresh=True``) does a REST ``fetch_application``;
        subsequent calls serve the cached list so owner checks on hot paths don't
        block on REST.
        """
        if self._owner_ids is None or force_refresh:
            application = await self.rest.fetch_application()
            if application.team:
                self._owner_ids = list(application.team.members.keys())
            else:
                self._owner_ids = [application.owner.id]
        return self._owner_ids

    async def _warm_owner_ids_on_start(self, _event: h.StartedEvent) -> None:
        """Populate the owner-id cache once at startup, off the interaction path."""
        await self.fetch_owner_ids()

    async def fetch_owners(self) -> list[h.User]:
        """Fetch all owners of the bot from the cache or from discord if not
        cached."""
        return [
            await self.fetch_user(owner_id) for owner_id in await self.fetch_owner_ids()
        ]

    async def fetch_owner(self, index: int = 0) -> h.User:
        """This method fetches a single owner of the bot from the cache or from
        discord if not cached"""
        return await self.fetch_user((await self.fetch_owner_ids())[index])


class ServerEmojiEnabledBot(CachedFetchBot):
    def __init__(
        self, *args: t.Any, emoji_servers: list[int] | None = None, **kwargs: t.Any
    ):
        super().__init__(*args, **kwargs)
        self._emoji_servers: list[int] = (
            emoji_servers if emoji_servers is not None else []
        )
        self.emoji: dict[str, h.Emoji] = {}

        # Refresh emoji once on startup and then periodically. Lightbulb v2's
        # lightbulb.ext.tasks no longer exists in v3, so we self-schedule with
        # an asyncio loop instead.
        _ = self.listen(h.StartingEvent)(self._refresh_emoji_on_start)

    async def refresh_emoji(self):
        for server in reversed(self._emoji_servers):
            guild = await self.fetch_guild(server)
            for emoji in await guild.fetch_emojis():
                self.emoji[emoji.name] = emoji

    async def _refresh_emoji_on_start(self, _event: h.StartingEvent):
        await self.refresh_emoji()
        _ = asyncio.create_task(self._refresh_emoji_loop())

    async def _refresh_emoji_loop(self, interval: int = 240):
        while True:
            await asyncio.sleep(interval)
            try:
                await self.refresh_emoji()
            except Exception:
                logging.exception("Failed to refresh server emoji")
