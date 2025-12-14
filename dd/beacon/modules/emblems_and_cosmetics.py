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

import lightbulb as lb

from ...common import cfg
from .autoposts import autopost_command_group, follow_control_command_maker

# Followable channel from which to pull messages for the command and autoposts
FOLLOWABLE_CHANNEL = cfg.followables["emblems_and_cosmetics"]


def register(bot: lb.BotApp):
    autopost_command_group.child(
        follow_control_command_maker(
            FOLLOWABLE_CHANNEL,
            "emblems_and_cosmetics",
            "Emblems and Cosmetics",
            "D2 Emblems and Cosmetics auto posts",
        )
    )
