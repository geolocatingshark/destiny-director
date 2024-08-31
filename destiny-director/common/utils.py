import asyncio as aio
import logging
import typing as t

import aiohttp
import hikari as h
import regex as re

re_user_side_emoji = re.compile(r"(<a?)?:(\w+)(~\d)*:(\d+>)?")


def construct_emoji_substituter(
    emoji_dict: t.Dict[str, h.Emoji],
) -> t.Callable[[re.Match], str]:
    """Constructs a substituter for user-side emoji to be used in re.sub"""

    def func(match: re.Match) -> str:
        maybe_emoji_name = str(match.group(2))
        return str(
            emoji_dict.get(maybe_emoji_name)
            or emoji_dict.get(maybe_emoji_name.lower())
            or match.group(0)
        )

    return func


class space:
    zero_width = "\u200b"
    hair = "\u200a"
    six_per_em = "\u2006"
    thin = "\u2009"
    punctuation = "\u2008"
    four_per_em = "\u2005"
    three_per_em = "\u2004"
    figure = "\u2007"
    en = "\u2002"
    em = "\u2003"


def get_ordinal_suffix(day: int) -> str:
    return (
        {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        if day not in (11, 12, 13)
        else "th"
    )


async def update_status(bot: h.GatewayBot, guild_count: int, test_env: bool):
    await bot.update_presence(
        activity=h.Activity(
            name="{} servers : )".format(guild_count) if not test_env else "DEBUG MODE",
            type=h.ActivityType.LISTENING,
        )
    )


async def follow_link_single_step(
    url: str, logger=logging.getLogger("main/" + __name__)
) -> str:
    async with aiohttp.ClientSession() as session:
        retries = 10
        retry_delay = 10
        for i in range(retries):
            async with session.get(url, allow_redirects=False) as resp:
                try:
                    return resp.headers["Location"]
                except KeyError:
                    # If we can't find the location key, warn and return the
                    # provided url itself
                    if resp.status >= 400:
                        logger.error(
                            "Could not find redirect for url "
                            + "{}, (status {})".format(url, resp.status)
                        )
                        if i < retries - 1:
                            logger.error("Retrying...")
                        await aio.sleep(retry_delay)
                        continue
                    else:
                        return url
