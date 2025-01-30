# Copyright © 2019-present gsfernandes81

# This file is part of "destiny-director".

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
import regex as re
from hmessage import HMessage as MessagePrototype

from ...common import cfg
from ...common.utils import accumulate
from .. import utils
from ..nav import NavigatorView, NavPages
from .autoposts import autopost_command_group, follow_control_command_maker

REFERENCE_DATE = dt.datetime(2023, 7, 14, 17, tzinfo=dt.timezone.utc)

FOLLOWABLE_CHANNEL = cfg.followables["xur"]

# This regex finds the lines that start with
# "Arrives:" or "Departs:"
# These lines are intended to be removed in code
rgx_find_arrives_departs_text = re.compile(r"\n\*\*(Arrives|Departs):\*\*[^\n]*")


class XurPages(NavPages):
    def preprocess_messages(
        self, messages: t.List[MessagePrototype | h.Message]
    ) -> MessagePrototype:
        # NOTE: This assumes that the xur message is sent with the
        # location gif as a link, not as an attachment
        # This will need to be updated if this is changed
        for m in messages:
            m.embeds = utils.filter_discord_autoembeds(m)
            # Suppress autoembeds
            m.content = (
                cfg.url_regex.sub(lambda x: f"<{x.group()}>", m.content or "")
                .replace("<<", "<")
                .replace(">>", ">")
            )

        msg_proto = (
            accumulate(
                [
                    MessagePrototype.from_message(m)
                    # .merge_embed_url_as_embed_image_into_embed()
                    # .merge_attachements_into_embed()
                    for m in messages
                ]
            )
            .merge_content_into_embed(1)
            .merge_attachements_into_embed(default_url=cfg.default_url)
        )

        # Remove duplicate Arrives/Departs text from anchor embed
        for embed in msg_proto.embeds:
            embed.description = rgx_find_arrives_departs_text.sub(
                "", embed.description or ""
            )

        return msg_proto


async def on_start(event: h.StartedEvent):
    global xur_pages
    xur_pages = await XurPages.from_channel(
        event.app,
        FOLLOWABLE_CHANNEL,
        history_len=12,
        period=dt.timedelta(days=7),
        reference_date=REFERENCE_DATE,
        no_data_message=MessagePrototype(
            embeds=[
                h.Embed(
                    description=(
                        "Xûr arrives at the Tower (Bazaar) every *Friday at reset* "
                        "(<t:1734109200:t>) and departs on *Tuesday at reset*."
                    ),
                    color=cfg.embed_default_color,
                ),
            ]
        ),
    )


@lb.command("xur", "Find out what Xur has and where Xur is")
@lb.implements(lb.SlashCommand)
async def xur_command(ctx: lb.Context):
    navigator = NavigatorView(pages=xur_pages)
    await navigator.send(ctx.interaction)


def register(bot):
    bot.command(xur_command)
    bot.listen()(on_start)

    autopost_command_group.child(
        follow_control_command_maker(FOLLOWABLE_CHANNEL, "xur", "Xur", "Xur auto posts")
    )
