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

import lightbulb as lb

SOURCE_CODE_NOTICE = """
```
Copyright © 2019-present gsfernandes81

This bot (destiny-director) is open source! You can find the source code at \
https://github.com/geolocatingshark/destiny-director.git

destiny-director is free software: you can redistribute it and/or modify it under the \
terms of the GNU Affero General Public License as published by the Free Software \
Foundation, either version 3 of the License, or (at your option) any later version.

"destiny-director" is distributed in the hope that it will be useful, but WITHOUT ANY \
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A \
PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License along with \
destiny-director. If not, see <https://www.gnu.org/licenses/>.
```
"""


@lb.command(
    "source_code", description="Get the source code of destiny-director / this bot"
)
@lb.implements(lb.SlashCommand)
async def source_code(ctx: lb.Context):
    await ctx.respond(SOURCE_CODE_NOTICE)


def register(bot: lb.BotApp):
    bot.command(source_code)
