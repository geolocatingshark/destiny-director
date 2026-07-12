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

"""Unit tests for :class:`RunCounts` — mirror progress derived from a ledger count."""

from dd.beacon.mirror_core import RunCounts


def test_from_state_counts_maps_each_state():
    c = RunCounts.from_state_counts(
        {"DELIVERED": 4, "FAILED": 2, "CANCELLED": 1, "PENDING": 3}
    )
    assert (c.delivered, c.failed, c.cancelled, c.pending) == (4, 2, 1, 3)


def test_missing_states_default_to_zero():
    c = RunCounts.from_state_counts({"DELIVERED": 2})
    assert (c.delivered, c.failed, c.cancelled, c.pending) == (2, 0, 0, 0)


def test_totals_and_resolved():
    c = RunCounts.from_state_counts(
        {"DELIVERED": 4, "FAILED": 2, "CANCELLED": 1, "PENDING": 3}
    )
    assert c.total == 10
    assert c.resolved == 7  # delivered + failed + cancelled
    assert c.throughput_resolved == 6  # excludes cancels


def test_completion_is_no_pending():
    assert RunCounts.from_state_counts({"DELIVERED": 3}).is_complete is True
    assert (
        RunCounts.from_state_counts({"DELIVERED": 2, "PENDING": 1}).is_complete is False
    )


def test_empty_counts_are_complete_but_zero_total():
    # An empty count reads complete (no pending); callers gate on total > 0 so an
    # no rows yet is never treated as finished.
    c = RunCounts.from_state_counts({})
    assert c.total == 0
    assert c.is_complete is True
