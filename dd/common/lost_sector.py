"""Shared rendering of the Lost Sector post used by both bots."""

import asyncio
import datetime as dt
import typing as t

import aiohttp
import hikari as h

from dd.hmessage import HMessage

from ..common import cfg, schemas
from ..common.utils import discord_error_logger, fetch_emoji_dict
from ..sector_accounting import sector_accounting
from .utils import (
    construct_emoji_substituter,
    follow_link_single_step,
    re_user_side_emoji,
    space,
)

_elements = ["solar", "void", "arc", "stasis", "strand"]


def _elements_to_emoji(elements: str):
    elements = elements.lower()
    present_elements = []
    for element in _elements:
        if element in elements:
            present_elements.append(f":{element}:")
    return present_elements


def format_data(sector: sector_accounting.Sector) -> str:
    expert_data = sector.expert_data
    master_data = sector.master_data

    champs_string = space.figure.join(
        ["Champions:"]
        + [
            f":{champ}:"
            for champ in set(expert_data.champions_list + master_data.champions_list)
        ]
    )
    shields_string = space.figure.join(
        ["Shields:"]
        + _elements_to_emoji(str(expert_data.shields_list + master_data.shields_list))
    )

    return "\n".join([champs_string, shields_string]) + "\n\n"


async def format_post(
    bot: h.GatewayBot | None = None,
    sectors: list[sector_accounting.Sector] | None = None,
    date: dt.datetime | None = None,
    emoji_dict: dict[str, h.Emoji] | None = None,
) -> HMessage:
    """Format a lost sector announcement message

    Args:
        bot (h.GatewayBot | None, optional): The bot instance. Must be specified if
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
        emoji_dict = t.cast(dict[str, h.Emoji], await fetch_emoji_dict(bot))

    if sectors is None:
        # from_gspread_url does blocking gspread network I/O; offload it so the
        # event loop keeps servicing other coroutines during the autopost.
        rotation = await asyncio.to_thread(
            sector_accounting.Rotation.from_gspread_url,
            cfg.sheets_ls_url,
            cfg.gsheets_credentials,
            buffer=5,
        )
        sectors = rotation(date)

    # Follow the hyperlink to have the newest image embedded
    try:
        ls_gif_url = await follow_link_single_step(cfg.lost_sector_gif_url)
    except aiohttp.InvalidURL:
        ls_gif_url = None

    embed = h.Embed(
        title="**Destiny 2**",
        description=(
            "## [World Lost Sectors](https://kyber3000.com/LS)\n"
            "\n"
            "Changes daily at <t:1753894800:t> local time.\n"
            "\n"
        ),
        color=cfg.embed_default_color,
        url="https://lostsectortoday.com/",
    )

    ls_extra_details_enabled = (
        await schemas.AutoPostSettings.get_lost_sector_details_enabled()
    )

    description = embed.description or ""

    for sector in sectors:
        sector: sector_accounting.Sector
        description += f":LS: **[{sector.name}]({sector.shortlink_gfx})**\n"
        if ls_extra_details_enabled:
            description += format_data(sector)

    if not ls_extra_details_enabled:
        description += "\n"

    description += (
        "Rewards:\n"
        + ":enhancement_core: Enhancement Core\n"
        + ":exotic_engram: Exotic Engram (If-Solo)\n"
        + ":legendary_weap: Legendary Weapon (If-Solo)\n"
        + "\n"
    )
    description += (
        "[View more details](https://lostsectortoday.com) ↗\n"
        "[Support Us](https://ko-fi.com/Kyber3000) ↗\n"
    )

    description = re_user_side_emoji.sub(
        construct_emoji_substituter(emoji_dict), description
    )

    if len(description) >= 4096:
        await discord_error_logger(
            ValueError("WARNING: Embed is greater than 4096 characters!"),
            operation="Lost sector embed",
        )
        # TODO: Mention owners for above
        description = description[:4096]

    embed.description = description

    if ls_gif_url:
        embed.set_image(ls_gif_url)

    return HMessage(embeds=[embed])


async def format_sector(sector: sector_accounting.Sector) -> str:
    """Formats a Sector object into an embed."""
    return f":LS: **[{sector.name}]({sector.shortlink_gfx})**\n"
