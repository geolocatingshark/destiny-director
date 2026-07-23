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

"""Pure-logic tests for command-usage tracking (no DB).

The in-Discord ``/stats`` commands (and their text-chart helpers) were removed once the
web dashboard replaced them; only the ``_should_track`` gate for the usage-counting hook
remains here.
"""

import hikari as h

from dd.beacon.extensions.statistics import _should_track


def test_tracks_user_facing_slash_commands():
    assert _should_track("xur", h.CommandType.SLASH)
    assert _should_track("lost sector", h.CommandType.SLASH)
    # Admin-created custom user-commands have arbitrary top-level names → tracked.
    assert _should_track("my_custom_cmd", h.CommandType.SLASH)


def test_excludes_owner_admin_groups():
    assert not _should_track("autopost xur", h.CommandType.SLASH)
    assert not _should_track("stats commands", h.CommandType.SLASH)
    assert not _should_track("mirror manual_add", h.CommandType.SLASH)
    assert not _should_track("testing storm", h.CommandType.SLASH)
    assert not _should_track("command preview", h.CommandType.SLASH)


def test_excludes_non_slash_commands():
    assert not _should_track("Edit", h.CommandType.MESSAGE)
    assert not _should_track("Some User Menu", h.CommandType.USER)
