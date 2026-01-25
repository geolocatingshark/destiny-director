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
import miru

import dd.beacon.extensions

from ..common import cfg

bot = h.GatewayBot(
    token=cfg.discord_token_beacon,
    intents=h.Intents.ALL_UNPRIVILEGED | h.Intents.MESSAGE_CONTENT,
    max_rate_limit=600,
)

client = lb.client_from_app(
    bot,
    cfg.test_env or (),  # Lightbulb enabled guilds
)


@bot.listen(h.StartingEvent)
async def on_starting_event(event: h.StartingEvent):
    await client.load_extensions_from_package(dd.beacon.extensions)
    await client.start()


miru.install(bot)
bot.run()
