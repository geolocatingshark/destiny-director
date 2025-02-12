import datetime as dt
import typing as t

import aiohttp
import hikari as h
import lightbulb as lb
from hmessage import HMessage as MessagePrototype

from ..common import cfg, schemas
from ..common.utils import fetch_emoji_dict
from ..sector_accounting import sector_accounting
from . import utils
from .utils import (
    construct_emoji_substituter,
    get_ordinal_suffix,
    re_user_side_emoji,
    space,
)


def _fmt_count(emoji: str, count: int, width: int) -> str:
    if count:
        return "{} x `{}`".format(
            emoji,
            str(count if count != -1 else "?").rjust(width, " "),
        )
    else:
        return ""


def format_counts(
    legend_data: sector_accounting.DifficultySpecificSectorData,
    master_data: sector_accounting.DifficultySpecificSectorData,
    emoji_dict: t.Dict[str, h.Emoji],
) -> str:
    len_bar = len(
        str(max(legend_data.barrier_champions, master_data.barrier_champions, key=abs))
    )
    len_oload = len(
        str(
            max(legend_data.overload_champions, master_data.overload_champions, key=abs)
        )
    )
    len_unstop = len(
        str(
            max(
                legend_data.unstoppable_champions,
                master_data.unstoppable_champions,
                key=abs,
            )
        )
    )
    len_arc = len(str(max(legend_data.arc_shields, master_data.arc_shields, key=abs)))
    len_void = len(
        str(max(legend_data.void_shields, master_data.void_shields, key=abs))
    )
    len_solar = len(
        str(max(legend_data.solar_shields, master_data.solar_shields, key=abs))
    )
    len_stasis = len(
        str(max(legend_data.stasis_shields, master_data.stasis_shields, key=abs))
    )
    len_strand = len(
        str(max(legend_data.strand_shields, master_data.strand_shields, key=abs))
    )

    data_strings = []

    for data in [legend_data, master_data]:
        champs_string = space.figure.join(
            filter(
                None,
                [
                    _fmt_count(emoji_dict["barrier"], data.barrier_champions, len_bar),
                    _fmt_count(
                        emoji_dict["overload"], data.overload_champions, len_oload
                    ),
                    _fmt_count(
                        emoji_dict["unstoppable"],
                        data.unstoppable_champions,
                        len_unstop,
                    ),
                ],
            )
        )
        shields_string = space.figure.join(
            filter(
                None,
                [
                    _fmt_count(emoji_dict["arc"], data.arc_shields, len_arc),
                    _fmt_count(emoji_dict["void"], data.void_shields, len_void),
                    _fmt_count(emoji_dict["solar"], data.solar_shields, len_solar),
                    _fmt_count(emoji_dict["stasis"], data.stasis_shields, len_stasis),
                    _fmt_count(emoji_dict["strand"], data.strand_shields, len_strand),
                ],
            )
        )
        data_string = f"{space.figure}|{space.figure}".join(
            filter(
                None,
                [
                    champs_string,
                    shields_string,
                ],
            )
        )
        data_strings.append(data_string)

    return (
        f"Expert:{space.figure}"
        + data_strings[0]
        + f"\nMaster:{space.hair}{space.figure}"
        + data_strings[1]
    )


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
        emoji_dict = await fetch_emoji_dict(bot)

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
        ),
        color=cfg.embed_default_color,
        url="https://lostsectortoday.com/",
    )
    embed.description += "\n\n" + (
        "**Rewards (If-Solo)**\n"
        + str(emoji_dict["exotic_engram"])
        + f"{space.three_per_em}Exotic {sector.reward}"
    )

    if await schemas.AutoPostSettings.get_lost_sector_legendary_weapons_enabled():
        embed.description += "\n\n" + (
            "**Legendary Weapons (If-Solo)**\n" + legendary_weapon_rewards
        )

        embed.description += "\n\n" + (
            "**Drop Rate (with no champions left)**\n" + "Expert: 70%\n"
            "Master: 100% + double perks on weapons"
        )

    embed.description += "\n\n" + (
        "**Champs and Shields**\n"
        + format_counts(sector.legend_data, sector.master_data, emoji_dict)
    )
    embed.description += "\n\n" + (
        "**Elementals**\n"
        + f"Surge: {space.punctuation}{space.hair}{space.hair}"
        + " ".join(surges)
        + f"\nThreat: {threat}"
    )
    embed.description += "\n\n" + (
        "**Modifiers**\n"
        + str(emoji_dict["swords"])
        + f"{space.three_per_em}{sector.to_sector_v1().modifiers}"
        + f"\n{overcharged_weapon_emoji}{space.three_per_em}Overcharged {sector.overcharged_weapon}"
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
