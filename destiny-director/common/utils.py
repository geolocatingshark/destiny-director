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


class FriendlyValueError(ValueError):
    pass


def check_number_of_layers(
    ln_names: list | int, min_layers: int = 1, max_layers: int = 3
):
    """Raises FriendlyValueError on too many layers of commands

    This is a simple helper function to check if ln_names is between min_layers and
    max_layers. If it is not, a FriendlyValueError is raised."""

    ln_name_length = len(ln_names) if ln_names is not int else ln_names

    if ln_name_length > max_layers:
        raise FriendlyValueError(
            "Discord does not support slash "
            + f"commands with more than {max_layers} layers"
        )
    elif ln_name_length < min_layers:
        raise ValueError(f"Too few ln_names provided, need at least {min_layers}")


def ensure_session(sessionmaker):
    """Decorator for functions that optionally want an sqlalchemy async session

    Provides an async session via the `session` parameter if one is not already
    provided via the same.

    Caution: Always put below `@classmethod` and `@staticmethod`"""

    def ensured_session(f: t.Coroutine):
        async def wrapper(*args, **kwargs):
            session = kwargs.pop("session", None)
            if session is None:
                async with sessionmaker() as session:
                    async with session.begin():
                        return await f(*args, **kwargs, session=session)
            else:
                return await f(*args, **kwargs, session=session)

        return wrapper

    return ensured_session


T = t.TypeVar("T")


def accumulate(iterable: t.Iterable[T]) -> T:
    final = iterable[0]
    for arg in iterable[1:]:
        final = final + arg
    return final
