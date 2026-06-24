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

"""Shared ``/source_code`` command for both bots."""

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


def make_source_command() -> type[lb.SlashCommand]:
    """Builds a fresh ``/source_code`` command class for a bot's loader."""

    class SourceCode(
        lb.SlashCommand,
        name="source_code",
        description="Get the source code of destiny-director / this bot",
    ):
        @lb.invoke
        async def invoke(self, ctx: lb.Context):
            await ctx.respond(SOURCE_CODE_NOTICE)

    return SourceCode
