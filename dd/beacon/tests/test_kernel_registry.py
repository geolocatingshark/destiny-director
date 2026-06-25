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

"""Unit tests for ``KernelWorkControlRegistry.in_progress_count`` (no I/O)."""

import typing as t

from dd.beacon.mirror_core import KernelWorkControl, KernelWorkControlRegistry


class _StubControl:
    def __init__(self, is_work_left_to_do: bool) -> None:
        self.is_work_left_to_do = is_work_left_to_do


def _registry_with(*work_left_flags: bool) -> KernelWorkControlRegistry:
    reg = KernelWorkControlRegistry()
    for i, flag in enumerate(work_left_flags):
        reg._registry[(None, i)] = t.cast(KernelWorkControl, _StubControl(flag))
    return reg


def test_in_progress_count_counts_only_unfinished() -> None:
    # Finished controls (is_work_left_to_do is False) can linger until release() pops
    # them, so they must not be counted as in progress.
    assert _registry_with(True, False, True, False).in_progress_count == 2


def test_in_progress_count_zero_when_empty() -> None:
    assert KernelWorkControlRegistry().in_progress_count == 0
