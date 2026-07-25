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

"""Unit tests for ``_status_footer`` — the card's one-line status verdict.

Pins each branch of the terminal verdict so the footer contract is explicit before it
grows a new state: in-progress while running; Completed once delivered; Cancelled only
when *nothing* was delivered; and the "with errors" suffix whenever a target failed.
"""

from time import perf_counter

from dd.beacon.extensions.mirror import _status_footer
from dd.beacon.mirror_core import MirrorOperationType, RunCounts, RunView


def _view(**counts: int) -> RunView:
    view = RunView(
        op=MirrorOperationType.SEND,
        src_ch_id=1,
        src_msg_id=1,
        start_time=perf_counter(),
    )
    view.counts = RunCounts(**counts)
    return view


def test_in_progress_when_not_final() -> None:
    # Counts are irrelevant before the run is final.
    assert _status_footer(_view(delivered=5), final=False) == "⏳ In progress"


def test_completed_when_delivered() -> None:
    assert _status_footer(_view(delivered=3), final=True) == "✅ Completed"


def test_cancelled_only_when_nothing_delivered() -> None:
    assert _status_footer(_view(cancelled=3), final=True) == "❌ Cancelled"


def test_partial_delivery_then_cancel_reads_completed() -> None:
    # Some landed before the cancel → the run counts as Completed, not Cancelled.
    assert _status_footer(_view(delivered=2, cancelled=1), final=True) == "✅ Completed"


def test_completed_with_errors_suffix() -> None:
    assert (
        _status_footer(_view(delivered=3, failed=1), final=True)
        == "✅ Completed with errors"
    )


def test_cancelled_with_errors_suffix() -> None:
    assert (
        _status_footer(_view(cancelled=2, failed=1), final=True)
        == "❌ Cancelled with errors"
    )
