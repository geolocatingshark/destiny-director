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

from ...common import cfg
from ...common.bot import CachedFetchBot

control_group_name = "ddv1"
if cfg.test_env:
    control_group_name = "dev_ddv1"

loader = lb.Loader()

kyber = lb.Group(control_group_name, "Commands for Kyber")


@kyber.register
class AllStop(lb.SlashCommand, name="all_stop", description="SHUT DOWN THE BOT"):
    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        await ctx.respond("Bot is going down now.")
        await bot.close()


@kyber.register
class Restart(lb.SlashCommand, name="restart", description="RESTART THE BOT"):
    @lb.invoke
    async def invoke(self, ctx: lb.Context):
        await ctx.respond("Bot is restarting now.")
        # Exits with a non 0 code which is picked up by railway.app
        # which restarts the bot
        sys.exit(1)


@kyber.register
class Info(lb.SlashCommand, name="info", description="Configuration state info"):
    @lb.invoke
    async def invoke(self, ctx: lb.Context):
        config_info = (
            "**Configuration Info**\n"
            f"- Control Discord Server ID: {cfg.control_discord_server_id}\n"
            f"- Test Environment: {cfg.test_env}\n"
            f"- Lost Sector Channel: <#{cfg.followables['lost_sector']}>\n"
            f"- Xur Channel: <#{cfg.followables['xur']}>\n"
        )
        await ctx.respond(config_info)


# No guilds= → inherits the client's default_enabled_guilds (control + test_env). The
# client-level owner hook gates every command, so no per-command check is needed.
loader.command(kyber)
