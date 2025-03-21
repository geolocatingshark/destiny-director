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

import logging

import aiodebug.log_slow_callbacks
import hikari as h
import lightbulb as lb
import logwood.compat
import miru
import uvloop
from lightbulb.ext import tasks

from ..common import cfg, schemas, utils
from . import help, modules
from .bot import CachedFetchBot, CustomHelpBot, ServerEmojiEnabledBot, UserCommandBot


class Bot(ServerEmojiEnabledBot, UserCommandBot, CachedFetchBot, CustomHelpBot):
    pass


uvloop.install()


bot = Bot(
    **cfg.lightbulb_params(
        include_message_content_intent=True,
        central_guilds_only=False,
        discord_token=cfg.discord_token_beacon,
    ),
    user_command_schema=schemas.UserCommand,
    help_class=help.HelpCommand,
    help_slash_command=True,
    emoji_servers=[cfg.kyber_discord_server_id],
)


logwood.compat.redirect_standard_logging()
logwood.basic_config()
aiodebug.log_slow_callbacks.enable(0.05)


@bot.listen()
async def on_start(event: lb.events.LightbulbStartedEvent):
    bot.d.guild_count = len(await bot.rest.fetch_my_guilds())
    await utils.update_status(bot, bot.d.guild_count, cfg.test_env)


@bot.listen()
async def on_guild_add(event: h.events.GuildJoinEvent):
    bot.d.guild_count += 1
    await utils.update_status(bot, bot.d.guild_count, cfg.test_env)


@bot.listen()
async def on_guild_rm(event: h.events.GuildLeaveEvent):
    bot.d.guild_count -= 1
    await utils.update_status(bot, bot.d.guild_count, cfg.test_env)


_modules = map(modules.__dict__.get, modules.__all__)

for module in _modules:
    logging.info(f"Loading module {module.__name__.split('.')[-1]}")
    module.register(bot)

tasks.load(bot)
miru.install(bot)
bot.run()
