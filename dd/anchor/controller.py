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

import sys

import lightbulb as lb

from ..common import cfg
from . import utils

control_group_name = "ddv1"
if cfg.test_env:
    control_group_name = "dev_ddv1"


@lb.command(
    control_group_name,
    "Commands for Kyber",
    guilds=[cfg.control_discord_server_id],
)
@lb.implements(lb.SlashCommandGroup)
async def kyber():
    pass


@kyber.child
@lb.command("all_stop", "SHUT DOWN THE BOT", guilds=[cfg.control_discord_server_id])
@lb.implements(lb.SlashSubCommand)
@utils.check_admin
async def all_stop(ctx: lb.Context):
    await ctx.respond("Bot is going down now.")
    await ctx.bot.close()


@kyber.child
@lb.command("restart", "RESTART THE BOT", guilds=[cfg.control_discord_server_id])
@lb.implements(lb.SlashSubCommand)
@utils.check_admin
async def restart(ctx: lb.Context):
    await ctx.respond("Bot is restarting now.")
    # Exits with a non 0 code which is picked up by railway.app
    # which restarts the bot
    sys.exit(1)


def register(bot: lb.BotApp) -> None:
    bot.command(kyber)


@kyber.child
@lb.command("info", "Configuration state info", guilds=[cfg.control_discord_server_id])
@lb.implements(lb.SlashSubCommand)
@utils.check_admin
async def info(ctx: lb.Context):
    config_info = (
        "**Configuration Info**\n"
        f"- Control Discord Server ID: {cfg.control_discord_server_id}\n"
        f"- Test Environment: {cfg.test_env}\n"
        f"- Lost Sector Channel: <#{cfg.followables["lost_sector"]}>\n"
        f"- Xur Channel: <#{cfg.followables["xur"]}>\n"
    )
    await ctx.respond(config_info)
