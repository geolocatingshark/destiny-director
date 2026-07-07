"""Shared rendering of the Lost Sector post used by both bots."""

import asyncio
import datetime as dt
import logging
import typing as t

import aiohttp
import hikari as h

from dd.hmessage import HMessage

from ..common import cfg, components, schemas
from ..common.utils import discord_error_logger, fetch_emoji_dict
from ..sector_accounting import sector_accounting
from .utils import (
    construct_emoji_substituter,
    follow_link_single_step,
    re_user_side_emoji,
    space,
)

_elements = ["solar", "void", "arc", "stasis", "strand"]

# Last-known-good rotation, keyed by post type. Populated on every successful load and
# served only if both the DB store and the gspread fallback are unreachable, so a
# transient outage never breaks an autopost.
_rotation_cache: dict[str, sector_accounting.Rotation] = {}

_LOST_SECTOR = "lost_sector"


async def load_rotation(buffer: int = 5) -> sector_accounting.Rotation:
    """Load the ``lost_sector`` rotation, preferring the DB JSON store.

    Resolution order: the ``RotationData['lost_sector']`` document (built via
    :meth:`Rotation.from_json`) → the live gspread Sheet (kept as a fallback during the
    cutover; blocking, so offloaded to a thread) → the last-known-good cache. The DB is
    consulted every call (it is cheap and lets editor saves take effect immediately);
    the cache exists only for the total-outage case. Raises only if every source fails
    and nothing was ever cached.
    """
    try:
        doc = await schemas.RotationData.get_data(_LOST_SECTOR)
    except Exception:
        logging.exception("Failed to read lost_sector rotation from the DB")
        doc = None

    if doc is not None:
        try:
            rotation = sector_accounting.Rotation.from_json(doc, buffer=buffer)
            _rotation_cache[_LOST_SECTOR] = rotation
            return rotation
        except Exception:
            logging.exception(
                "Stored lost_sector rotation JSON is malformed; falling back to gspread"
            )

    try:
        # from_gspread_url does blocking gspread network I/O; offload it so the event
        # loop keeps servicing other coroutines during the autopost.
        rotation = await asyncio.to_thread(
            sector_accounting.Rotation.from_gspread_url,
            cfg.sheets_ls_url,
            cfg.gsheets_credentials,
            buffer=buffer,
        )
        _rotation_cache[_LOST_SECTOR] = rotation
        return rotation
    except Exception:
        cached = _rotation_cache.get(_LOST_SECTOR)
        if cached is not None:
            logging.exception(
                "Failed to load lost_sector rotation; serving last-known-good cache"
            )
            return cached
        raise


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
        rotation = await load_rotation(buffer=5)
        sectors = rotation(date)

    # Follow the hyperlink to have the newest image embedded
    try:
        ls_gif_url = await follow_link_single_step(cfg.lost_sector_gif_url)
    except aiohttp.InvalidURL:
        ls_gif_url = None

    ls_extra_details_enabled = (
        await schemas.AutoPostSettings.get_lost_sector_details_enabled()
    )

    # Components V2: the former embed's title + description become one text display and
    # the trailing image a full-width media gallery (mirroring the old set_image). The
    # markdown is unchanged so the rendered post looks the same as the embed did.
    description = (
        "**Destiny 2**\n"
        "## [World Lost Sectors](https://kyber3000.com/LS)\n"
        "\n"
        "Changes daily at <t:1753894800:t> local time.\n"
        "\n"
    )

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

    # Components V2 messages cap total text at 4000 characters.
    if len(description) >= 4000:
        await discord_error_logger(
            ValueError("WARNING: CV2 text is greater than 4000 characters!"),
            operation="Lost sector post",
        )
        # TODO: Mention owners for above
        description = description[:4000]

    container = h.impl.ContainerComponentBuilder(
        accent_color=h.Color(cfg.embed_default_color)
    )
    container.add_text_display(description)
    if ls_gif_url:
        # URL-referenced (Discord fetches it) rather than uploaded — the gif is ~15 MB,
        # which would 413 on upload and re-download from the host on every send.
        container.add_component(components.url_media_gallery(ls_gif_url))

    return HMessage(components=[container])


async def format_sector(sector: sector_accounting.Sector) -> str:
    """Formats a Sector object into an embed."""
    return f":LS: **[{sector.name}]({sector.shortlink_gfx})**\n"
