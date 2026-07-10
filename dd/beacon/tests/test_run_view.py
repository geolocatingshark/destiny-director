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

"""Unit tests for :class:`RunView` — in-memory mirror progress accounting (no I/O)."""

from time import perf_counter

from dd.beacon.mirror_core import (
    MirrorOperationType,
    RunFailure,
    RunView,
)
from dd.common.utils import ErrorClass


def _view(
    total: int = 3, op: MirrorOperationType = MirrorOperationType.SEND
) -> RunView:
    return RunView(
        op=op, src_ch_id=1, src_msg_id=99, total=total, start_time=perf_counter()
    )


def _fail(ref: str, cls: ErrorClass = ErrorClass.PERMANENT, dead: bool = False):
    return RunFailure(
        reference_code=ref, error_class=cls, sample_message="x", confirmed_dead=dead
    )


def test_counts_and_completion():
    v = _view(total=3)
    v.on_delivered(10)
    v.on_transient(11)  # attempted, not yet resolved → retrying
    v.on_failed(12, _fail("PERM01"))
    assert (v.delivered, v.failed, v.cancelled_count) == (1, 1, 0)
    assert v.retrying == 1
    assert v.not_yet_tried == 0
    assert v.resolved == 2
    assert v.throughput_resolved == 2
    assert v.is_complete is False

    v.on_delivered(11)  # the retrying one converges
    assert v.retrying == 0
    assert v.resolved == 3
    assert v.is_complete is True


def test_not_yet_tried_counts_untouched_dests():
    v = _view(total=5)
    v.on_delivered(10)
    assert v.not_yet_tried == 4  # 5 total, 1 attempted


def test_cancelled_completes_run():
    v = _view(total=2)
    v.on_delivered(10)
    v.on_cancelled(11)
    assert v.cancelled_count == 1
    assert v.resolved == 2
    assert v.is_complete is True


def test_cancel_is_ignored_for_resolved_dest():
    v = _view(total=1)
    v.on_delivered(10)
    v.on_cancelled(10)  # already delivered → not re-counted as cancelled
    assert v.cancelled_count == 0
    assert v.delivered == 1


def test_delivered_clears_prior_failure():
    v = _view(total=1)
    v.on_failed(10, _fail("PERM01"))
    assert v.failed == 1
    v.on_delivered(10)  # a retry succeeded
    assert v.failed == 0
    assert v.delivered == 1
    assert 10 not in v.failures


def test_supersede_completes_regardless():
    v = _view(total=10)
    v.on_delivered(1)
    assert v.is_complete is False
    v.superseded_by_edit = True
    assert v.is_complete is True


def test_failure_breakdown_groups_by_ref_most_common_first():
    v = _view(total=5)
    v.on_failed(10, _fail("AAA", ErrorClass.PERMANENT))
    v.on_failed(11, _fail("AAA", ErrorClass.PERMANENT))
    v.on_failed(12, _fail("BBB", ErrorClass.TRANSIENT))
    breakdown = v.failure_breakdown
    assert [(g.reference_code, g.count) for g in breakdown] == [("AAA", 2), ("BBB", 1)]
    assert breakdown[0].error_class is ErrorClass.PERMANENT


def test_has_permanent_and_not_confirmed_dead():
    v = _view(total=3)
    v.on_failed(10, _fail("AAA", ErrorClass.PERMANENT, dead=True))
    v.on_failed(11, _fail("BBB", ErrorClass.PERMANENT, dead=False))
    v.on_failed(12, _fail("CCC", ErrorClass.TRANSIENT, dead=False))
    assert v.has_permanent is True
    # Only permanent-but-not-confirmed-dead is surfaced for a human.
    assert set(v.not_confirmed_dead) == {11}
