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

"""Pure-logic tests for the hourly Distortion rotation (no Discord/DB/clock dep)."""

import datetime as dt

from dd.beacon.extensions.distortion import (
    REFERENCE_DATE,
    _format_countdown,
    distortion_at,
)


def test_reference_date_is_cosmodrome():
    current, upcoming, until = distortion_at(REFERENCE_DATE)
    assert current == "Cosmodrome"
    assert upcoming == "European Dead Zone"
    assert until == dt.timedelta(hours=1)


def test_six_hours_later_is_nessus():
    current, upcoming, until = distortion_at(REFERENCE_DATE + dt.timedelta(hours=6))
    assert current == "Nessus"
    assert upcoming == "Cosmodrome"
    assert until == dt.timedelta(hours=1)


def test_seven_hours_wraps_back_to_cosmodrome():
    current, upcoming, _ = distortion_at(REFERENCE_DATE + dt.timedelta(hours=7))
    assert current == "Cosmodrome"
    assert upcoming == "European Dead Zone"


def test_mid_hour_countdown():
    now = REFERENCE_DATE + dt.timedelta(hours=6, minutes=30)
    current, upcoming, until = distortion_at(now)
    assert current == "Nessus"
    assert upcoming == "Cosmodrome"
    assert until == dt.timedelta(minutes=30)


def test_community_tracker_sample():
    # unix 1781446950 is a known Nessus hour on the community tracker.
    now = dt.datetime.fromtimestamp(1781446950, dt.UTC)
    current, _, until = distortion_at(now)
    assert current == "Nessus"
    assert until == dt.timedelta(minutes=37, seconds=30)


def test_format_countdown():
    assert _format_countdown(dt.timedelta(minutes=39)) == "39m"
    assert _format_countdown(dt.timedelta(hours=1, minutes=5)) == "1h 5m"
    assert _format_countdown(dt.timedelta(hours=1)) == "1h 0m"
