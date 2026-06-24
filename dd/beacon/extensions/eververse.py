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

import datetime as dt

import lightbulb as lb

from ...common import cfg
from ..nav import make_navigator_command, setup_nav_pages
from .autoposts import follow_control_command_maker

loader = lb.Loader()

REFERENCE_DATE = dt.datetime(2023, 7, 18, 17, tzinfo=dt.UTC)

EVERVERSE_WEEKLY = cfg.followables["eververse"]

_pages = setup_nav_pages(
    loader,
    followable_channel=EVERVERSE_WEEKLY,
    history_len=4,
    period=dt.timedelta(days=7),
    reference_date=REFERENCE_DATE,
)

eververse_group = lb.Group("eververse", "Find out about the eververse items")
eververse_group.register(
    make_navigator_command(
        _pages,
        name="weekly",
        description="Find out about this weeks eververse items",
    )
)

loader.command(eververse_group)

follow_control_command_maker(
    EVERVERSE_WEEKLY,
    "eververse_weekly",
    "Eververse weekly",
    "Eververse weekly auto posts",
)
