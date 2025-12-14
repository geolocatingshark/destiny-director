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

import hikari as h
import lightbulb as lb
from hmessage import HMessage as MessagePrototype

from ...common import cfg
from ...common.utils import accumulate
from .. import utils
from ..nav import NavigatorView, NavPages
from .autoposts import autopost_command_group, follow_control_command_maker

# Remove the below ignore line to enable the module
IGNORE = True

# Followable channel from which to pull messages for the command and autoposts
FOLLOWABLE_CHANNEL = 123456789  # cfg.followables[<Something Here>]

# CODE FOR PAGES BELOW. CAN BE SAFELY REMOVED IF ONLY AUTOPOSTS ARE NEEDED

# Reference date and update period for the pages
REFERENCE_DATE = dt.datetime(2023, 7, 14, 17, tzinfo=dt.timezone.utc)
UPDATE_PERIOD = dt.timedelta(days=7)


class Pages(NavPages):
    def preprocess_messages(
        self, messages: t.List[MessagePrototype | h.Message]
    ) -> MessagePrototype:
        for m in messages:
            m.embeds = utils.filter_discord_autoembeds(m)

        msg_proto = (
            accumulate([MessagePrototype.from_message(m) for m in messages])
            .merge_content_into_embed()
            .merge_attachements_into_embed(default_url=cfg.default_url)
        )

        return msg_proto


async def on_start(event: h.StartedEvent):
    global pages
    pages = await Pages.from_channel(
        event.app,
        FOLLOWABLE_CHANNEL,
        reference_date=REFERENCE_DATE,
        period=UPDATE_PERIOD,
    )


@lb.command("xur", "Find out what Xur has and where Xur is")
@lb.implements(lb.SlashCommand)
async def slash_command(ctx: lb.Context):
    navigator = NavigatorView(pages=pages)
    await navigator.send(ctx.interaction)


# CODE FOR PAGES BELOW. CAN BE SAFELY REMOVED IF ONLY AUTOPOSTS ARE NEEDED


def register(bot: lb.BotApp):
    # Remove the below if only autoposts are needed
    bot.command(slash_command)
    bot.listen()(on_start)
    # Remove the above if only autoposts are needed

    autopost_command_group.child(
        follow_control_command_maker(FOLLOWABLE_CHANNEL, "xur", "Xur", "Xur auto posts")
    )
