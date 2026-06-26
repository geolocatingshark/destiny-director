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

# Content checks for the beacon bot's detailed /help pages.

from dd.beacon.help_details import COMMAND_GROUP_DETAIL, HELP_DETAILS
from dd.common.help import render_detail_sections


def test_command_group_detail_is_registered() -> None:
    assert COMMAND_GROUP_DETAIL in HELP_DETAILS
    # The key must match the /command group's registered name.
    assert COMMAND_GROUP_DETAIL.command == "command"


def test_all_details_render_with_a_title_heading() -> None:
    for detail in HELP_DETAILS:
        sections = render_detail_sections(detail)
        assert sections and sections[0].startswith("## ")


def test_detail_keys_are_unique() -> None:
    keys = [d.command.casefold() for d in HELP_DETAILS]
    assert len(keys) == len(set(keys))
