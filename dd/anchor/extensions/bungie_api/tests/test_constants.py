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

import pytest

from dd.anchor.extensions.bungie_api.constants import likely_emoji_name


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Hand Cannon", "hand_cannon"),
        ("Pulse-Rifle", "pulse_rifle"),
        ("Exotic Engram", "exotic_engram"),
        ("already_ok", "already_ok"),
        ("Mixed-Case Name", "mixed_case_name"),
    ],
)
def test_likely_emoji_name(name: str, expected: str):
    assert likely_emoji_name(name) == expected
