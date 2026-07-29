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

"""A guild-channel-scoped gateway cache for the beacon bot.

Beacon is a fan-out announcement/mirror bot present in thousands of guilds, but it only
ever *reads* a small set of channels — the mirror source + destination channels resolved
by the low-load reachability sweep. hikari's default cache proactively stores **every**
channel of **every** guild from each ``GUILD_CREATE``; at beacon's guild count that
guild-channel cache is the single largest slice of resident RAM (and Railway bills on
memory-over-time). This :class:`hikari.impl.CacheImpl` subclass refuses to store any
guild channel whose id is not in a live "relevant" set.

Everything else is cached normally via the trimmed ``CacheSettings`` the bot is built
with (``only_my_member=True`` keeps the member cache at the bot's own member per guild;
guilds + roles stay cached because the permission checks — the mirror auto-disable sweep
in :mod:`dd.beacon.utils` and the ``/autopost`` invoker gate — resolve
``guild.get_roles()`` from the cache). Only the channel population is scoped here.

This is safe because every cache read in the codebase is *fetch-through*: see
:class:`dd.common.bot.CachedFetchBot`, whose ``fetch_channel`` falls back to a REST
``fetch_channel`` on a cache miss. So refusing a channel is never a correctness
problem — at worst it turns a would-be cache hit into one REST call on the
(deliberately low-load) sweep. Delivery itself sends by channel id over REST and needs
no cached channel.

``relevant_channels`` is a **live** set: the owner (``dd.beacon.__main__``) refreshes
its contents in place from the mirror config, and each gateway (re)connect's
``GUILD_CREATE`` burst is filtered against whatever it holds at that moment. A channel
added between refreshes simply isn't cached until the next refresh + reconnect —
harmless, per above.
"""

from __future__ import annotations

import hikari as h
from hikari.impl.cache import CacheImpl


class ScopedChannelCacheImpl(CacheImpl):
    """A ``CacheImpl`` that only caches guild channels present in ``relevant_channels``.

    See the module docstring for the why. Only :meth:`set_guild_channel` is overridden;
    hikari's ``update_guild_channel`` delegates to it, so gating this one method covers
    both the ``GUILD_CREATE`` bulk populate and ``CHANNEL_UPDATE``. All other cache
    behaviour (guilds, roles, members, messages, gets, deletes) is inherited unchanged.
    """

    __slots__ = ("_relevant_channels",)

    def __init__(
        self,
        app: h.traits.RESTAware,
        settings: h.impl.CacheSettings,
        relevant_channels: set[int],
    ) -> None:
        super().__init__(app, settings)
        # A live, externally-mutated set of channel ids worth caching (mirror sources +
        # destinations). Held by reference so the owner can refresh it in place.
        self._relevant_channels = relevant_channels

    def _should_cache_channel(self, channel_id: int) -> bool:
        return int(channel_id) in self._relevant_channels

    def set_guild_channel(self, channel: h.PermissibleGuildChannel, /) -> None:
        if not self._should_cache_channel(channel.id):
            return
        super().set_guild_channel(channel)
