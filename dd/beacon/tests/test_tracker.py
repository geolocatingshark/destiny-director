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

"""Unit tests for :class:`KernelWorkTracker` accounting / ``_apply_outcome``."""

from dd.beacon.mirror_core import (
    KernelFailure,
    KernelSuccess,
    KernelWorkTracker,
    MirrorOperationType,
)
from dd.common.utils import ErrorClass


def _tracker(targets: dict[int, int | None], retry_threshold: int = 3):
    return KernelWorkTracker(
        source_channel_id=1,
        source_message_id=99,
        targets=targets,
        mirror_operation_type=MirrorOperationType.SEND,
        retry_threshold=retry_threshold,
    )


def _fail(ch: int, error_class: ErrorClass, code: str = "ABC123") -> KernelFailure:
    return KernelFailure(
        channel_id=ch,
        exc=ValueError("boom"),
        error_class=error_class,
        reference_code=code,
    )


def test_success_records_message_and_clears_schedule() -> None:
    t = _tracker({10: None})
    t.report_scheduled(10)
    t._apply_outcome(KernelSuccess(channel_id=10, message_id=555))  # noqa: SLF001
    assert t.successful_targets == {10: 555}
    assert 10 not in t.targets_to_schedule
    assert t.failed_targets == {}


def test_permanent_failure_excluded_from_scheduling_immediately() -> None:
    t = _tracker({10: None}, retry_threshold=3)
    t.report_scheduled(10)
    t._apply_outcome(_fail(10, ErrorClass.PERMANENT))  # noqa: SLF001
    # Despite tries (1) < threshold (3), a permanent failure is never rescheduled.
    assert 10 not in t.targets_to_schedule
    assert 10 in t.failed_targets


def test_transient_failure_retried_until_threshold() -> None:
    t = _tracker({10: None}, retry_threshold=2)
    t.report_scheduled(10)
    t._apply_outcome(_fail(10, ErrorClass.TRANSIENT))  # noqa: SLF001
    # One try done, below threshold -> still schedulable.
    assert 10 in t.targets_to_schedule
    t.report_scheduled(10)
    t._apply_outcome(_fail(10, ErrorClass.TRANSIENT))  # noqa: SLF001
    # Hit the threshold -> failed, not schedulable.
    assert 10 not in t.targets_to_schedule
    assert 10 in t.failed_targets


def test_failure_breakdown_counts_by_reference_code() -> None:
    t = _tracker({10: None, 11: None, 12: None})
    for ch in (10, 11):
        t.report_scheduled(ch)
        t._apply_outcome(_fail(ch, ErrorClass.PERMANENT, code="PERM01"))  # noqa: SLF001
    t.report_scheduled(12)
    t._apply_outcome(_fail(12, ErrorClass.TRANSIENT, code="TRAN01"))  # noqa: SLF001

    breakdown = t.failure_breakdown
    by_code = {g.reference_code: g for g in breakdown}
    assert by_code["PERM01"].count == 2
    assert by_code["PERM01"].error_class is ErrorClass.PERMANENT
    assert by_code["TRAN01"].count == 1
    # Most-common first.
    assert breakdown[0].reference_code == "PERM01"


def test_newly_sent_only_includes_fresh_sends() -> None:
    # ch 10 starts as a fresh send (None); ch 11 starts as an existing edit target.
    t = _tracker({10: None, 11: 700})
    t.report_scheduled(10)
    t._apply_outcome(KernelSuccess(channel_id=10, message_id=900))  # noqa: SLF001
    t.report_scheduled(11)
    t._apply_outcome(KernelSuccess(channel_id=11, message_id=700))  # noqa: SLF001
    assert t.newly_sent == {10: 900}
    assert t.successful_targets == {10: 900, 11: 700}


def test_cancel_flag_stops_scheduling() -> None:
    t = _tracker({10: None, 11: None})
    t._cancelled = True  # noqa: SLF001
    assert t.targets_to_schedule == {}


def test_failed_targets_excludes_later_success() -> None:
    # A transient failure then a success should not be counted as failed.
    t = _tracker({10: None}, retry_threshold=2)
    t.report_scheduled(10)
    t._apply_outcome(_fail(10, ErrorClass.TRANSIENT))  # noqa: SLF001
    t.report_scheduled(10)
    t._apply_outcome(KernelSuccess(channel_id=10, message_id=1))  # noqa: SLF001
    assert t.failed_targets == {}
    assert t.successful_targets == {10: 1}
