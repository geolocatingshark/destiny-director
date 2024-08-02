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
import inspect
import logging
import typing as t
from asyncio import Semaphore, create_task
from random import randint

import aiohttp
import hikari as h
import lightbulb as lb
from hmessage import HMessage as MessagePrototype
from toolbox.members import calculate_permissions

from . import cfg


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


async def follow_link_single_step(
    url: str, logger=logging.getLogger("main/" + __name__)
) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.get(url, allow_redirects=False) as resp:
            try:
                return resp.headers["Location"]
            except KeyError:
                # If we can't find the location key, warn and return the
                # provided url itself
                logger.info(
                    "Could not find redirect for url "
                    + "{}, returning as is".format(url)
                )
                return url


class FriendlyValueError(ValueError):
    pass


def get_function_name() -> str:
    """Get the name of the function this was called from"""
    return inspect.stack()[1][3]


def check_number_of_layers(
    ln_names: list | int, min_layers: int = 1, max_layers: int = 3
):
    """Raises FriendlyValueError on too many layers of commands"""

    ln_name_length = len(ln_names) if ln_names is not int else ln_names

    if ln_name_length > max_layers:
        raise FriendlyValueError(
            "Discord does not support slash "
            + f"commands with more than {max_layers} layers"
        )
    elif ln_name_length < min_layers:
        raise ValueError(f"Too few ln_names provided, need at least {min_layers}")


error_logger_semaphore = Semaphore(1)


async def discord_error_logger(
    bot: h.GatewayBot, e: Exception, error_reference: int = None
):
    """Logs discord errors to the log channel and the console"""

    if not error_reference:
        error_reference = randint(1000000, 9999999)

    alerts_channel = await bot.fetch_channel(cfg.alerts_channel)

    async with error_logger_semaphore:
        task = create_task(
            alerts_channel.send(
                f"Exception {type(e)} with reference {error_reference} occurred"
            )
        )
        logging.error(f"Error reference: {error_reference}")
        logging.exception(e)
        await task


T = t.TypeVar("T")


def accumulate(iterable: t.Iterable[T]) -> T:
    final = iterable[0]
    for arg in iterable[1:]:
        final = final + arg
    return final


async def check_invoker_has_perms(
    ctx: lb.Context,
    permissions: h.Permissions | t.List[h.Permissions],
    all_required=False,
):
    bot: lb.BotApp = ctx.bot
    invoker = ctx.author
    if not isinstance(permissions, (list, tuple)):
        permissions = (permissions,)

    channel = await bot.rest.fetch_channel(ctx.channel_id)
    member = await bot.rest.fetch_member(ctx.guild_id, invoker.id)
    invoker_perms = calculate_permissions(member, channel)

    if all_required:
        return all(
            [permission == (permission & invoker_perms) for permission in permissions]
        )
    else:
        return any(
            [permission == (permission & invoker_perms) for permission in permissions]
        )


async def check_invoker_is_owner(ctx: lb.Context):
    bot: lb.BotApp = ctx.bot
    invoker = ctx.author
    return invoker.id in await bot.fetch_owner_ids()


def daily_reset_period(now: dt.datetime = None) -> t.Tuple[dt.datetime]:
    now = (now or dt.datetime.now(tz=dt.timezone.utc)) - dt.timedelta(hours=17)
    now = dt.datetime(now.year, now.month, now.day, 17, 0, 0, tzinfo=dt.timezone.utc)
    start = now
    end = start + dt.timedelta(days=1)
    return start, end


def weekly_reset_period(now: dt.datetime = None) -> t.Tuple[dt.datetime]:
    now = (now or dt.datetime.now(tz=dt.timezone.utc)) - dt.timedelta(hours=17)
    now = dt.datetime(now.year, now.month, now.day, 17, 0, 0, tzinfo=dt.timezone.utc)
    start = now - dt.timedelta(days=(now.weekday() - 1) % 7)
    # Ends at the same day and time next week
    end = start + dt.timedelta(days=7)
    return start, end


def xur_period(now: dt.datetime = None) -> t.Tuple[dt.datetime]:
    now = (now or dt.datetime.now(tz=dt.timezone.utc)) - dt.timedelta(hours=17)
    now = dt.datetime(now.year, now.month, now.day, 17, 0, 0, tzinfo=dt.timezone.utc)
    start = now - dt.timedelta(days=(now.weekday() + 3) % 7)
    # Ends at the same day and time next week
    end = start + dt.timedelta(days=7)
    return start, end


def get_ordinal_suffix(day: int) -> str:
    return (
        {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        if day not in (11, 12, 13)
        else "th"
    )


async def wait_till_lightbulb_started(bot: lb.BotApp):
    if not bot.d.has_lb_started:
        await bot.wait_for(lb.LightbulbStartedEvent, timeout=None)
        bot.d.has_lightbulb_started = True


def filter_discord_autoembeds(msg: h.Message | MessagePrototype):
    content = msg.content or ""
    filtered_embeds = []

    if not content:
        # If there is no content
        # there will be no autoembeds
        return msg.embeds

    for embed in msg.embeds or []:
        embed: h.Embed
        embed_url = embed.url or ""
        if embed_url not in content and (
            embed.title
            or embed.description
            or embed.fields
            or embed.footer
            or embed.author
        ):
            filtered_embeds.append(embed)
    return filtered_embeds


def followable_name(*, id: int) -> str | int:
    return next((key for key, value in cfg.followables.items() if value == id), id)


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
