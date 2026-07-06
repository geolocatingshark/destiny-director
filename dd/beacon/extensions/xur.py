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
from typing import override

import hikari as h
import lightbulb as lb
import regex as re

from dd.hmessage import HMessage

from ...common import cfg
from ...common.components import build_container
from ...common.utils import accumulate
from .. import utils
from ..nav import NavPages, make_navigator_command, setup_nav_pages
from .autoposts import follow_control_command_maker

loader = lb.Loader()

REFERENCE_DATE = dt.datetime(2023, 7, 14, 17, tzinfo=dt.UTC)

FOLLOWABLE_CHANNEL = cfg.followables["xur"]

# This regex finds the lines that start with
# "Arrives:" or "Departs:"
# These lines are intended to be removed in code
rgx_find_arrives_departs_text = re.compile(r"\n\*\*(Arrives|Departs):\*\*[^\n]*")


class XurPages(NavPages):
    @override
    def preprocess_messages(self, messages: list[h.Message]) -> HMessage:
        if not messages:
            return self.no_data_message

        # Components V2 posts (the migrated Xûr format) carry components, not embeds, so
        # the embed/content post-processing below doesn't apply — return them as-is,
        # mirroring the base NavPages.preprocess_messages.
        if any(
            h.MessageFlag.IS_COMPONENTS_V2 in (m.flags or h.MessageFlag.NONE)
            for m in messages
        ):
            return accumulate([HMessage.from_message(m) for m in messages])

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
                    HMessage.from_message(m)
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


_pages = setup_nav_pages(
    loader,
    pages_cls=XurPages,
    followable_channel=FOLLOWABLE_CHANNEL,
    history_len=12,
    period=dt.timedelta(days=7),
    reference_date=REFERENCE_DATE,
    cv2=True,
    no_data_message=HMessage(
        components=[
            build_container(
                [
                    "Xûr arrives at the Tower (Bazaar) every *Friday at reset* "
                    "(<t:1734109200:t>) and departs on *Tuesday at reset*."
                ]
            )
        ]
    ),
)

loader.command(
    make_navigator_command(
        _pages,
        name="xur",
        description="Find out what Xur has and where Xur is",
    )
)

follow_control_command_maker(FOLLOWABLE_CHANNEL, "xur", "Xur", "Xur auto posts")
