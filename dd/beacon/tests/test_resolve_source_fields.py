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

"""Unit tests for ``_resolve_source_fields`` (progress-card source links).

A recovery or uncached-delete card carries no cached source message/channel, but the
run still knows its ``src_ch_id`` — the card must name its source channel from that id
instead of degrading to "Unknown".
"""

from unittest.mock import AsyncMock, MagicMock

import hikari as h
import pytest

from dd.beacon.extensions import mirror

pytestmark = pytest.mark.asyncio


async def test_resolves_channel_from_src_ch_id_without_a_cached_message() -> None:
    channel = MagicMock(spec=h.GuildTextChannel)
    channel.name = "announcements"
    channel.id = 555
    channel.guild_id = 900
    bot = MagicMock()
    bot.fetch_channel = AsyncMock(return_value=channel)

    _mlink, msummary, clink, cname = await mirror._resolve_source_fields(
        bot, None, None, src_ch_id=555
    )

    assert cname == "announcements"
    assert clink == "https://discord.com/channels/900/555"
    assert msummary == "Unknown"  # a deleted/uncached source has no message
    bot.fetch_channel.assert_awaited_once_with(555)  # resolved from src_ch_id


async def test_degrades_to_unknown_without_any_source_reference() -> None:
    bot = MagicMock()
    bot.fetch_channel = AsyncMock()

    result = await mirror._resolve_source_fields(bot, None, None, src_ch_id=None)

    assert result == ("", "Unknown", "", "Unknown")
    bot.fetch_channel.assert_not_awaited()
