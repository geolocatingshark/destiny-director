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

import asyncio as aio
import logging
import typing as t

import aiocron
import hikari as h
import lightbulb as lb
from hmessage import HMessage as MessagePrototype

from ..common import cfg, schemas
from ..common.lost_sector import format_post
from . import utils
from .autopost import make_autopost_control_commands

logger = logging.getLogger(__name__)


async def discord_announcer(
    bot: lb.BotApp,
    channel_id: int,
    construct_message_coro: t.Coroutine[t.Any, t.Any, MessagePrototype] = None,
    check_enabled: bool = False,
    enabled_check_coro: t.Coroutine[t.Any, t.Any, bool] = None,
    publish_message: bool = True,
):
    while True:
        retries = 0
        try:
            if check_enabled and not await enabled_check_coro():
                return
            hmessage = await construct_message_coro(bot=bot)
        except Exception as e:
            logger.exception(e)
            retries += 1
            await aio.sleep(min(2**retries, 300))
        else:
            break

    logger.info("Announcing lost sector to discord")
    await utils.send_message(
        bot,
        hmessage,
        channel_id=channel_id,
        crosspost=publish_message,
        deduplicate=True,
    )
    logger.info("Announced lost sector to discord")


@lb.option(
    "option", "Enable or disable", str, choices=["Enable", "Disable"], required=True
)
@lb.command(
    "details",
    "Control whether lost sector additional details and counts are sent out",
    auto_defer=True,
    pass_options=True,
)
@lb.implements(lb.SlashSubCommand)
async def control_lost_sector_details(ctx: lb.Context, option: str):
    """Enable or disable lost sector legendary weapon announcements"""

    desired_setting: bool = True if option.lower() == "enable" else False
    current_setting = await schemas.AutoPostSettings.get_lost_sector_details_enabled()

    if desired_setting == current_setting:
        return await ctx.respond(
            f"Lost sector details are already {'enabled' if desired_setting else 'disabled'}"
        )

    await schemas.AutoPostSettings.set_lost_sector_details(enabled=desired_setting)
    await ctx.respond(
        f"Lost sector details are now {'enabled' if desired_setting else 'disabled'}"
    )


def sub_group(parent: lb.CommandLike, name: str, description: str):
    @lb.command(name, description)
    @lb.implements(lb.SlashSubGroup)
    def _():
        pass

    parent.child(_)

    return _


@lb.command("ls_update", "Update a lost sector post", ephemeral=True, auto_defer=True)
@lb.implements(lb.MessageCommand)
async def ls_update(ctx: lb.MessageContext):
    """Correct a mistake in the lost sector announcement"""

    if ctx.author.id not in cfg.admins:
        await ctx.respond("Only admins can use this command")
        return

    msg_to_update: h.Message = ctx.options.target

    if not schemas.AutoPostSettings.get_lost_sector_enabled():
        await ctx.respond("Please enable autoposts before using this cmd")

    logger.info("Correcting posts")

    await ctx.edit_last_response("Updating post now")

    message = await format_post(bot=ctx.app)
    await msg_to_update.edit(**message.to_message_kwargs())
    await ctx.edit_last_response("Post updated")


async def on_start_schedule_autoposts(event: lb.LightbulbStartedEvent):
    # Run every day at 17:00 UTC
    @aiocron.crontab("0 17 * * *", start=True)
    # Use below crontab for testing to post every minute
    # @aiocron.crontab("* * * * *", start=True)
    async def autopost_ls():
        await discord_announcer(
            event.app,
            channel_id=cfg.followables["lost_sector"],
            check_enabled=True,
            enabled_check_coro=schemas.AutoPostSettings.get_lost_sector_enabled,
            construct_message_coro=format_post,
        )


def register(bot: lb.BotApp) -> None:
    autopost_control_parent_group = make_autopost_control_commands(
        "ls",
        schemas.AutoPostSettings.get_lost_sector_enabled,
        schemas.AutoPostSettings.set_lost_sector,
        cfg.followables["lost_sector"],
        format_post,
        message_announcer_coro=discord_announcer,
    )

    autopost_control_parent_group.child(control_lost_sector_details)

    bot.command(autopost_control_parent_group)

    bot.command(ls_update)
    bot.listen(lb.LightbulbStartedEvent)(on_start_schedule_autoposts)
