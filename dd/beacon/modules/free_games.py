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

import hikari as h
import lightbulb as lb

from ...common import cfg
from ..bot import CachedFetchBot
from .autoposts import autopost_command_group, follow_control_command_maker

# Followable channel from which to pull messages for the command and autoposts
FOLLOWABLE_CHANNEL = cfg.followables["free_games"]

HELP_STRING = "See the current free games on The Epic Store, etc"


async def refresh_message_for_command(bot: CachedFetchBot):
    global last_message_in_channel_id
    global last_message_in_channel

    async for message in (await bot.fetch_channel(FOLLOWABLE_CHANNEL)).fetch_history():
        last_message_in_channel = message
        last_message_in_channel_id = message.id
        break


async def on_message_create(
    event: h.MessageCreateEvent,
):
    global last_message_in_channel
    global last_message_in_channel_id

    if event.channel_id == FOLLOWABLE_CHANNEL:
        last_message_in_channel = event.message
        last_message_in_channel_id = event.channel_id


async def on_message_update(
    event: h.MessageUpdateEvent,
):
    global last_message_in_channel
    global last_message_in_channel_id

    if (
        event.channel_id == FOLLOWABLE_CHANNEL
        and event.message.id == last_message_in_channel_id
    ):
        last_message_in_channel = event.message
        last_message_in_channel_id = event.channel_id


async def on_message_delete(
    event: h.MessageDeleteEvent,
):
    if (
        event.channel_id == FOLLOWABLE_CHANNEL
        and event.message_id == last_message_in_channel_id
    ):
        await refresh_message_for_command(event.app)


async def on_start(event: h.StartedEvent):
    await refresh_message_for_command(event.app)


@lb.command(
    "free",
    "See the current free games on The Epic Store, etc",
)
@lb.implements(lb.SlashCommandGroup)
async def slash_command_group():
    pass


@slash_command_group.child
@lb.command("games", HELP_STRING)
@lb.implements(lb.SlashSubCommand)
async def slash_subcommand(ctx: lb.Context):
    global last_message_in_channel

    await ctx.respond(last_message_in_channel)


def register(bot: lb.BotApp):
    bot.command(slash_command_group)
    bot.listen()(on_start)
    bot.listen()(on_message_create)
    bot.listen()(on_message_update)
    bot.listen()(on_message_delete)

    autopost_command_group.child(
        follow_control_command_maker(
            FOLLOWABLE_CHANNEL, "free_games", "Free Games", HELP_STRING
        )
    )
