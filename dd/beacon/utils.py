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

import inspect
import typing as t

import hikari as h
import lightbulb as lb
from toolbox.members import calculate_permissions

from dd.hmessage import HMessage


def get_function_name() -> str:
    """Get the name of the function this was called from"""
    return inspect.stack()[1][3]


async def check_invoker_has_perms(
    ctx: lb.Context,
    permissions: h.Permissions | list[h.Permissions],
    all_required: bool = False,
):
    bot: h.RESTAware = ctx.client.app
    invoker = ctx.user
    if not isinstance(permissions, (list, tuple)):
        permissions_ = (permissions,)
    else:
        permissions_ = permissions

    if not ctx.guild_id:
        return False

    channel = await bot.rest.fetch_channel(ctx.channel_id)

    if isinstance(channel, h.GuildThreadChannel):
        channel = await bot.rest.fetch_channel(channel.parent_id)

    if not isinstance(channel, h.PermissibleGuildChannel):
        return False

    member = await bot.rest.fetch_member(ctx.guild_id, invoker.id)
    invoker_perms = calculate_permissions(member, channel)

    if all_required:
        return all(
            [permission == (permission & invoker_perms) for permission in permissions_]
        )
    else:
        return any(
            [permission == (permission & invoker_perms) for permission in permissions_]
        )


def filter_discord_autoembeds(msg: h.Message | HMessage) -> list[h.Embed]:
    content = msg.content or ""
    filtered_embeds: list[h.Embed] = []

    if not content:
        # If there is no content
        # there will be no autoembeds
        return list(msg.embeds)

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


type self_ = t.Any


@t.overload
def ignore_own_user(
    func: t.Callable[
        [self_, h.MessageCreateEvent | h.MessageUpdateEvent], t.Awaitable[None]
    ],
) -> t.Callable[
    [self_, h.MessageCreateEvent | h.MessageUpdateEvent], t.Awaitable[None]
]: ...


@t.overload
def ignore_own_user(
    func: t.Callable[[h.MessageCreateEvent], t.Coroutine[t.Any, t.Any, None]],
) -> t.Callable[[h.MessageCreateEvent], t.Coroutine[t.Any, t.Any, None]]: ...


@t.overload
def ignore_own_user(
    func: t.Callable[[h.MessageUpdateEvent], t.Coroutine[t.Any, t.Any, None]],
) -> t.Callable[[h.MessageUpdateEvent], t.Coroutine[t.Any, t.Any, None]]: ...


def ignore_own_user(
    func: t.Callable[..., t.Awaitable[None]],
) -> t.Callable[..., t.Awaitable[None]]:
    # Use *args so this works both as a plain event listener (event,) and as a
    # method decorator (self, event,) — inspect.ismethod is always False at class
    # definition time, so the distinction cannot be made at decoration time.
    async def _wrapped(*args: t.Any) -> None:
        event = args[-1]
        if not isinstance(event.app, h.GatewayBot):
            return
        own_user = event.app.get_me()
        if own_user and event.author_id == own_user.id:
            # Never respond to self or mirror self
            return
        await func(*args)

    return _wrapped
