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
import logging
import typing as t

import hikari as h
import lightbulb as lb
from hmessage import HMessage as MessagePrototype

from ...common import cfg
from ...common.lost_sector import get_ordinal_suffix
from ...common.utils import accumulate
from .. import utils
from ..bot import CachedFetchBot, ServerEmojiEnabledBot, UserCommandBot
from ..nav import NavigatorView, NavPages
from .autoposts import autopost_command_group, follow_control_command_maker

REFERENCE_DATE = dt.datetime(2023, 7, 20, 17, tzinfo=dt.timezone.utc)

FOLLOWABLE_CHANNEL = cfg.followables["lost_sector"]


class SectorMessages(NavPages):
    bot: ServerEmojiEnabledBot

    def preprocess_messages(self, messages: t.List[h.Message | MessagePrototype]):
        for m in messages:
            m.embeds = utils.filter_discord_autoembeds(m)
        processed_messages = [
            MessagePrototype.from_message(m).merge_content_into_embed(prepend=False)
            # Remove merge_attachements_into_embed since it cause embeds to disappear
            # Did not investigate further as this functionality was not used in the
            # last 3 months at least
            # .merge_attachements_into_embed(default_url=cfg.default_url)
            for m in messages
        ]

        processed_message = accumulate(processed_messages)

        # Date correction
        try:
            title = str(processed_message.embeds[0].title)
            if "Lost Sector Today" in title:
                date = messages[0].timestamp
                suffix = get_ordinal_suffix(date.day)
                title = title.replace(
                    "Today", f"for {date.strftime('%B %-d')}{suffix}", 1
                )
                processed_message.embeds[0].title = title
        except Exception as e:
            e.add_note("Exception trying to replace date in lost sector title")
            logging.exception(e)

        return processed_message


async def on_start(event: h.StartedEvent):
    global sectors
    sectors = await SectorMessages.from_channel(
        event.app,
        FOLLOWABLE_CHANNEL,
        history_len=14,
        lookahead_len=0,
        period=dt.timedelta(days=1),
        reference_date=REFERENCE_DATE,
    )


@lb.command("ls", "Find out about today's lost sector")
@lb.implements(lb.SlashCommandGroup)
async def ls_group():
    pass


@ls_group.child
@lb.command("today", "Find out about today's lost sector")
@lb.implements(lb.SlashSubCommand)
async def ls_today_command(ctx: lb.Context):
    navigator = NavigatorView(pages=sectors)
    await navigator.send(ctx.interaction)


@lb.command("lost", "Find out about today's lost sector")
@lb.implements(lb.SlashCommandGroup)
async def ls_group_2():
    pass


@ls_group_2.child
@lb.command("sector", "Find out about today's lost sector")
@lb.implements(lb.SlashSubCommand)
async def lost_sector_command(ctx: lb.Context):
    navigator = NavigatorView(pages=sectors)
    await navigator.send(ctx.interaction)


def register(bot: t.Union[CachedFetchBot, UserCommandBot]):
    bot.command(ls_group)
    bot.command(ls_group_2)
    bot.listen()(on_start)

    autopost_command_group.child(
        follow_control_command_maker(
            FOLLOWABLE_CHANNEL, "lost_sector", "Lost sector", "Lost sector auto posts"
        )
    )
