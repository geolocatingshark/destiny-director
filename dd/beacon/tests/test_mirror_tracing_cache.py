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

"""Unit tests for the M4 traced-mirror cache eviction (no DB)."""

import pytest

from dd.beacon.extensions import mirror_tracing


@pytest.fixture(autouse=True)
def _clean_cache():
    mirror_tracing.non_legacy_mirrors.clear()
    yield
    mirror_tracing.non_legacy_mirrors.clear()


def test_forget_removes_dest_and_drops_empty_key() -> None:
    mirror_tracing.non_legacy_mirrors[100] = [200, 300]
    mirror_tracing.forget_traced_mirror(100, 200)
    assert mirror_tracing.non_legacy_mirrors[100] == [300]

    mirror_tracing.forget_traced_mirror(100, 300)
    # Key dropped once its dest list empties.
    assert 100 not in mirror_tracing.non_legacy_mirrors


def test_forget_is_noop_for_unknown_entries() -> None:
    mirror_tracing.non_legacy_mirrors[100] = [200]
    mirror_tracing.forget_traced_mirror(999, 1)
    mirror_tracing.forget_traced_mirror(100, 999)
    assert mirror_tracing.non_legacy_mirrors[100] == [200]


def test_forget_removes_all_duplicate_dests() -> None:
    # Defensive: even if a dest somehow got appended twice, all copies are removed.
    mirror_tracing.non_legacy_mirrors[100] = [200, 200, 300]
    mirror_tracing.forget_traced_mirror(100, 200)
    assert mirror_tracing.non_legacy_mirrors[100] == [300]
