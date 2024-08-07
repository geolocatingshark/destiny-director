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
import logging
import typing as t

import aiohttp
import hikari as h
import lightbulb as lb
from hmessage import HMessage as MessagePrototype

from ...common import cfg, schemas
from ...common.lost_sector import format_counts, get_emoji_dict
from ...common.utils import (
    construct_emoji_substituter,
    get_ordinal_suffix,
    re_user_side_emoji,
    space,
)
from ...sector_accounting import sector_accounting
from .. import utils
from ..bot import CachedFetchBot, ServerEmojiEnabledBot, UserCommandBot
from ..nav import NO_DATA_HERE_EMBED, NavigatorView, NavPages
from .autoposts import autopost_command_group, follow_control_command_maker

REFERENCE_DATE = dt.datetime(2023, 7, 20, 17, tzinfo=dt.timezone.utc)

FOLLOWABLE_CHANNEL = cfg.followables["lost_sector"]


async def format_sector(
    bot: lb.BotApp | None = None,
    sector: sector_accounting.Sector | None = None,
    secondary_image: h.Attachment | None = None,
    secondary_embed_title: str | None = "",
    secondary_embed_description: str | None = "",
    date: dt.datetime | None = None,
    emoji_dict: t.Dict[str, h.Emoji] | None = None,
) -> MessagePrototype:
    """Format a lost sector announcement message

    Args:
        bot (lb.BotApp | None, optional): The bot instance. Must be specified if
        emoji_dict is not.

        sector (sector_accounting.Sector | None, optional): The sector to announce.
        Fetches today's sector if not specified

        secondary_image (h.Attachment | None, optional): The secondary image to embed.
        Defaults to None.

        secondary_embed_title (str | None, optional): The title of the secondary embed.
        Defaults to "".

        secondary_embed_description (str | None, optional): The description of the
        secondary embed. Defaults to "".

        date (dt.datetime | None, optional): The date to mention in the post announce.
        Defaults to None.

        emoji_dict (t.Dict[str, h.Emoji] | None, optional): The emoji dictionary must
        be specified if the bot is not specified.
    """

    if emoji_dict is None:
        if bot is None:
            raise ValueError("bot must be provided if emoji_dict is not")
        emoji_dict = await get_emoji_dict(bot)

    if sector is None:
        sector: sector_accounting.Sector = sector_accounting.Rotation.from_gspread_url(
            cfg.sheets_ls_url, cfg.gsheets_credentials, buffer=5
        )()

    # Follow the hyperlink to have the newest image embedded
    try:
        ls_gfx_url = await utils.follow_link_single_step(sector.shortlink_gfx)
    except aiohttp.InvalidURL:
        ls_gfx_url = None

    # Surges to emojis
    surges = []
    for surge in sector.surges:
        surges += [str(emoji_dict.get(surge) or emoji_dict.get(surge.lower()))]

    # Threat to emoji
    threat = emoji_dict.get(sector.threat) or emoji_dict.get(sector.threat.lower())

    overcharged_weapon_emoji = (
        "⚔️" if sector.overcharged_weapon.lower() in ["sword", "glaive"] else "🔫"
    )

    if "(" in sector.name or ")" in sector.name:
        sector_name = sector.name.split("(")[0].strip()
        sector_location = sector.name.split("(")[1].split(")")[0].strip()
    else:
        sector_name = sector.name
        sector_location = None

    # Legendary weapon rewards
    legendary_weapon_rewards = sector.legendary_rewards

    legendary_weapon_rewards = re_user_side_emoji.sub(
        construct_emoji_substituter(emoji_dict), legendary_weapon_rewards
    )

    if date:
        suffix = get_ordinal_suffix(date.day)
        title = f"Lost Sector for {date.strftime('%B %-d')}{suffix}"
    else:
        title = "Lost Sector Today"

    embed = h.Embed(
        title=f"**{title}**",
        description=(
            f"{emoji_dict['LS']}{space.three_per_em}{sector_name.strip()}\n"
            + (
                f"{emoji_dict['location']}{space.three_per_em}{sector_location.strip()}"
                if sector_location
                else ""
            )
            + "\n"
        ),
        color=cfg.embed_default_color,
        url="https://lostsectortoday.com/",
    )
    embed.add_field(
        name="Rewards (If-Solo)",
        value=str(emoji_dict["exotic_engram"])
        + f"{space.three_per_em}Exotic {sector.reward}",
    )

    if await schemas.AutoPostSettings.get_lost_sector_legendary_weapons_enabled():
        embed.add_field(
            "Legendary Weapons (If-Solo)",
            legendary_weapon_rewards,
        )

        embed.add_field(
            "Drop Rate (with no champions left)",
            "Expert: 70%\n" "Master: 100% + double perks on weapons",
        )

    embed.add_field(
        name="Champs and Shields",
        value=format_counts(sector.legend_data, sector.master_data, emoji_dict),
    )
    embed.add_field(
        name="Elementals",
        value=f"Surge: {space.punctuation}{space.hair}{space.hair}"
        + " ".join(surges)
        + f"\nThreat: {threat}",
    )
    embed.add_field(
        name="Modifiers",
        value=str(emoji_dict["swords"])
        + f"{space.three_per_em}{sector.to_sector_v1().modifiers}"
        + f"\n{overcharged_weapon_emoji}{space.three_per_em}Overcharged {sector.overcharged_weapon}",
    )

    if ls_gfx_url:
        embed.set_image(ls_gfx_url)

    if secondary_image:
        embed2 = h.Embed(
            title=secondary_embed_title,
            description=secondary_embed_description,
            color=cfg.kyber_pink,
        )
        embed2.set_image(secondary_image)
        embeds = [embed, embed2]
    else:
        embeds = [embed]

    return MessagePrototype(embeds=embeds)


class SectorMessages(NavPages):
    bot: ServerEmojiEnabledBot

    def preprocess_messages(self, messages: t.List[h.Message | MessagePrototype]):
        for m in messages:
            m.embeds = utils.filter_discord_autoembeds(m)
        processed_messages = [
            MessagePrototype.from_message(m)
            .merge_content_into_embed(prepend=False)
            .merge_attachements_into_embed(default_url=cfg.default_url)
            for m in messages
        ]

        processed_message = utils.accumulate(processed_messages)

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

    async def lookahead(
        self, after: dt.datetime
    ) -> t.Dict[dt.datetime, MessagePrototype]:
        start_date = after
        sector_on = sector_accounting.Rotation.from_gspread_url(
            cfg.sheets_ls_url, cfg.gsheets_credentials, buffer=1
        )

        lookahead_dict = {}

        for date in [start_date + self.period * n for n in range(self.lookahead_len)]:
            try:
                sector = sector_on(date)
            except KeyError:
                # A KeyError will be raised if TBC is selected for the google sheet
                # In this case, we will just return a message saying that there is no data
                lookahead_dict = {
                    **lookahead_dict,
                    date: MessagePrototype(embeds=[NO_DATA_HERE_EMBED]),
                }
            else:
                # Follow the hyperlink to have the newest image embedded
                lookahead_dict = {
                    **lookahead_dict,
                    date: await format_sector(
                        sector=sector, date=date, emoji_dict=self.bot.emoji
                    ),
                }

        return lookahead_dict


async def on_start(event: h.StartedEvent):
    global sectors
    sectors = await SectorMessages.from_channel(
        event.app,
        FOLLOWABLE_CHANNEL,
        history_len=14,
        lookahead_len=7,
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
