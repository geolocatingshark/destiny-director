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

"""Entry point for the beacon (main) Discord bot.

Run with ``python -OOm dd.beacon``. Wires up the hikari client, miru and
lightbulb, loads the ``dd.beacon.extensions`` and starts the gateway.
"""

import hikari as h
import lightbulb as lb
import miru

import dd.beacon.extensions
import dd.beacon.extensions.user_commands
from dd.beacon.extensions.statistics import track_command_usage

from ..common import cfg, schemas
from ..common.auth import owner_check_error_handler
from ..common.bot import CachedFetchBot, ServerEmojiEnabledBot
from ..common.discord_logging import (
    aclose_discord_logging,
    install_command_error_reporting,
    install_discord_logging,
)
from ..common.extension_loader import load_extensions_strict
from ..common.lifecycle import consume_exit_code

bot = ServerEmojiEnabledBot(
    token=cfg.discord_token_beacon,
    intents=h.Intents.ALL_UNPRIVILEGED | h.Intents.MESSAGE_CONTENT,
    max_rate_limit=600,
    emoji_servers=[cfg.kyber_discord_server_id],
)

client = lb.client_from_app(
    bot,
    cfg.test_env or (),  # Lightbulb enabled guilds
    hooks=[track_command_usage],  # client-wide command-usage counter
)

# Make the bot injectable as CachedFetchBot (its concrete subclass type) in
# addition to the hikari.GatewayBot registration lightbulb adds automatically.
client.di.registry_for(lb.di.Contexts.DEFAULT).register_value(CachedFetchBot, bot)
# Make the live command client injectable so the dynamic user-command system can
# reach it to (re)register commands at runtime.
client.di.registry_for(lb.di.Contexts.DEFAULT).register_value(lb.Client, client)

# Render owner-gate rejections ephemerally, ahead of the catch-all alert reporter so
# they never page the alerts channel.
client.error_handler(owner_check_error_handler)

# Surface any otherwise-unhandled command failure to the alerts channel, labelled
# with the command that failed.
install_command_error_reporting(client)


@bot.listen(h.StartingEvent)
async def on_starting_event(_event: h.StartingEvent):
    await schemas.wait_for_db()
    await load_extensions_strict(client, dd.beacon.extensions)
    await dd.beacon.extensions.user_commands.resync_user_commands(client, sync=False)
    await client.start()


@bot.listen(h.StartedEvent)
async def on_start_install_logging(_event: h.StartedEvent):
    await install_discord_logging(bot, bot_name="beacon")


@bot.listen(h.StoppingEvent)
async def on_stopping_event(_event: h.StoppingEvent):
    await aclose_discord_logging()
    await schemas.db_engine.dispose()


miru.install(bot)
bot.run()
# Exit on the main thread with the code requested by a lifecycle command (0 if none).
# This is reliable where a SystemExit raised inside an interaction-callback task is not.
raise SystemExit(consume_exit_code())
