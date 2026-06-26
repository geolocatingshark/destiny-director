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

import datetime as dt
import typing as t
from typing import override

import hikari as h
import lightbulb as lb

from dd.hmessage import HMessage

from ...common import cfg
from ...common.bot import ServerEmojiEnabledBot
from ...common.lost_sector import format_post, load_rotation
from ...common.utils import accumulate
from .. import utils
from ..nav import (
    NO_DATA_HERE_EMBED,
    NavPages,
    make_navigator_command,
    setup_nav_pages,
)
from .autoposts import follow_control_command_maker

loader = lb.Loader()

REFERENCE_DATE = dt.datetime(2023, 7, 20, 17, tzinfo=dt.UTC)

FOLLOWABLE_CHANNEL = cfg.followables["lost_sector"]


class SectorMessages(NavPages):
    @override
    def preprocess_messages(self, messages: list[h.Message]):
        if not messages:
            return self.no_data_message
        for m in messages:
            m.embeds = utils.filter_discord_autoembeds(m)
        processed_messages = [
            HMessage.from_message(m).merge_content_into_embed(prepend=False)
            # Remove merge_attachements_into_embed since it cause embeds to disappear
            # Did not investigate further as this functionality was not used in the
            # last 3 months at least
            # .merge_attachements_into_embed(default_url=cfg.default_url)
            for m in messages
        ]

        processed_message = accumulate(processed_messages)

        return processed_message

    @override
    async def lookahead(self, after: dt.datetime) -> dict[dt.datetime, HMessage]:
        start_date = after
        bot = t.cast(ServerEmojiEnabledBot, self.bot)
        sector_on = await load_rotation(buffer=1)

        lookahead_dict = {}

        for date in [
            start_date + self.period * n for n in range(0, self.lookahead_len - 1)
        ]:
            try:
                sectors = sector_on(date)
            except KeyError:
                # A KeyError will be raised if TBC is selected for the google sheet
                # In this case, we will just return a message saying that there
                # is no data
                lookahead_dict = {
                    **lookahead_dict,
                    date: HMessage(embeds=[NO_DATA_HERE_EMBED]),
                }
            else:
                lookahead_dict = {
                    **lookahead_dict,
                    date: await format_post(
                        bot=bot,
                        sectors=sectors,
                        date=date,
                        emoji_dict=bot.emoji,
                    ),
                }

        return lookahead_dict


_pages = setup_nav_pages(
    loader,
    pages_cls=SectorMessages,
    followable_channel=FOLLOWABLE_CHANNEL,
    history_len=14,
    lookahead_len=7,
    period=dt.timedelta(days=1),
    reference_date=REFERENCE_DATE,
)

ls_group = lb.Group("ls", "Find out about today's lost sector")
ls_group.register(
    make_navigator_command(
        _pages, name="today", description="Find out about today's lost sector"
    )
)

ls_group_2 = lb.Group("lost", "Find out about today's lost sector")
ls_group_2.register(
    make_navigator_command(
        _pages, name="sector", description="Find out about today's lost sector"
    )
)

loader.command(ls_group)
loader.command(ls_group_2)

follow_control_command_maker(
    FOLLOWABLE_CHANNEL, "lost_sector", "Lost sector", "Lost sector auto posts"
)
