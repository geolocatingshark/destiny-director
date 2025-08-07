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
import inspect
import typing as t

import hikari as h
import lightbulb as lb
from hmessage import HMessage as MessagePrototype
from toolbox.members import calculate_permissions

from ..common import cfg


def get_function_name() -> str:
    """Get the name of the function this was called from"""
    return inspect.stack()[1][3]


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


def ignore_self(func):
    async def wrapped_func(event: h.MessageEvent):
        if event.author_id == event.app.get_me().id:
            # Never respond to self or mirror self
            return

        return await func(event)

    return wrapped_func


def ignore_self_for_method(func):
    async def wrapped_func(self, event: h.MessageEvent):
        if event.author_id == event.app.get_me().id:
            # Never respond to self or mirror self
            return

        return await func(self, event)

    return wrapped_func
