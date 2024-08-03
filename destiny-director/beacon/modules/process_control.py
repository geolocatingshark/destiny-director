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

import sys

import lightbulb as lb

from ...common import cfg
from .. import utils


@lb.command(
    "process_control",
    "Shutdown and restart commands",
    guilds=[cfg.control_discord_server_id],
    hidden=True,
)
@lb.implements(lb.SlashCommandGroup)
def process_control_command_group():
    pass


@process_control_command_group.child
@lb.command(
    "shutdown",
    "USE WITH CAUTION: Shuts down the bot! Cannot be restarted from discord!",
    auto_defer=True,
)
@lb.implements(lb.SlashSubCommand)
async def shutdown_command(ctx: lb.Context):
    if not await utils.check_invoker_is_owner(ctx):
        await ctx.respond("Only a bot owner can use this command")
    else:
        try:
            await ctx.respond("Bot is going down **now**")
            await ctx.bot.close()
        except:
            pass
        finally:
            sys.exit(0)


@process_control_command_group.child
@lb.command("restart", "Restarts the bot", auto_defer=True)
@lb.implements(lb.SlashSubCommand)
async def restart_command(ctx: lb.Context):
    if not await utils.check_invoker_is_owner(ctx):
        await ctx.respond("Only a bot owner can use this command")
    else:
        try:
            await ctx.respond("Bot is restarting **now**")
            await ctx.bot.close()
        except:
            pass
        finally:
            sys.exit(1)


def register(bot: lb.BotApp):
    bot.command(process_control_command_group)
