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


async def format_post(
    bot: lb.BotApp | None = None,
    sectors: sector_accounting.Sector | None = None,
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

    if sectors is None:
        sectors: t.List[sector_accounting.Sector] = (
            sector_accounting.Rotation.from_gspread_url(
                cfg.sheets_ls_url, cfg.gsheets_credentials, buffer=5
            )(date)
        )

    # Follow the hyperlink to have the newest image embedded
    try:
        ls_gif_url = await utils.follow_link_single_step(cfg.lost_sector_gif_url)
    except aiohttp.InvalidURL:
        ls_gif_url = None

    embed = h.Embed(
        title="**Destiny 2**",
        description=(
            "## [World Lost Sectors](https://kyber3000.com/LS)\n"
            "\n"
            "Changes daily at (<t:1753894800:t>) TO RECHECK FORMAT\n"
            "\n"
        ),
        color=cfg.embed_default_color,
        url="https://lostsectortoday.com/",
    )

    for sector in sectors:
        sector: sector_accounting.Sector
        embed.description += f":LS: **[{sector.name}]({sector.shortlink_gfx})**\n"

    embed.description += (
        "\n"
        + "Rewards:\n"
        + ":enhancement_core: Enhancement Core\n"
        + ":exotic_engram: Exotic Engram (If-Solo)\n"
        + ":legendary_weap: Legendary Weapon (If-Solo)\n"
    )
    embed.description += (
        "[View more details](https://lostsectortoday.com) ↗ "
        "| [Support Us](https://ko-fi.com/Kyber3000) ↗"
    )

    embed.description = re_user_side_emoji.sub(
        construct_emoji_substituter(emoji_dict), embed.description
    )

    embed.set_thumbnail(cfg.kyber_ls_thumbnail)

    if ls_gif_url:
        embed.set_image(ls_gif_url)

    return MessagePrototype(embeds=[embed])


async def format_sector(sector: sector_accounting.Sector) -> str:
    """Formats a Sector object into an embed."""
    return f":LS: **[{sector.name}]({sector.shortlink_gfx})**\n"
