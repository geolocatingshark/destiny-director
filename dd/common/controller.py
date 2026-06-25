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

"""Shared bot-administration command group for both bots.

Factory mirroring ``make_source_command``: each call builds a *fresh* group named after
the bot (``/anchor`` or ``/beacon``) with restart / stop / info subcommands. Lightbulb
command objects carry per-client registration state, so a fresh group is built per call
rather than shared across the two clients.

The factory applies ``owner_only`` to each subcommand itself rather than relying on a
client-wide gate: anchor gates its whole client, but beacon does not, so the gate must
travel with the commands (harmless/idempotent on anchor). The wrappers scope
registration to the control guild.

``stop`` exits cleanly (code 0) and only truly stops a service whose restart policy is
not ``ALWAYS``. Dev beacon and both anchors are ``ON_FAILURE``; prod beacon is
``ALWAYS`` and would respawn, so flip it to ``ON_FAILURE`` at the dev→main cutover.
``restart`` exits non-zero and works under any restart-on-failure policy.
"""

import sys

import lightbulb as lb

from . import cfg
from .auth import owner_only
from .bot import CachedFetchBot


def make_controller_group(bot_name: str) -> lb.Group:
    """Build a fresh bot-administration group named after ``bot_name``.

    Args:
        bot_name: The group name and top-level command, e.g. ``"anchor"`` or
            ``"beacon"`` (yields ``/anchor restart`` etc.).
    """
    group = lb.Group(bot_name, "Bot administration")

    @group.register
    class Restart(
        lb.SlashCommand,
        name="restart",
        description="Restart the bot",
        hooks=[owner_only],
    ):
        @lb.invoke
        async def invoke(self, ctx: lb.Context):
            await ctx.respond("Bot is restarting now.")
            # Exit non-zero so Railway's restart-on-failure policy respawns the bot.
            sys.exit(1)

    @group.register
    class Stop(
        lb.SlashCommand,
        name="stop",
        description="Shut down the bot",
        hooks=[owner_only],
    ):
        @lb.invoke
        async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
            await ctx.respond("Bot is going down now.")
            # Clean exit (0): only actually stops if the service isn't set to always
            # restart (see module docstring re: prod beacon).
            await bot.close()

    @group.register
    class Info(
        lb.SlashCommand,
        name="info",
        description="Configuration state info",
        hooks=[owner_only],
    ):
        @lb.invoke
        async def invoke(self, ctx: lb.Context):
            await ctx.respond(
                "**Configuration Info**\n"
                f"- Control Discord Server ID: {cfg.control_discord_server_id}\n"
                f"- Test Environment: {cfg.test_env}\n"
                f"- Lost Sector Channel: <#{cfg.followables['lost_sector']}>\n"
                f"- Xur Channel: <#{cfg.followables['xur']}>\n"
            )

    return group
