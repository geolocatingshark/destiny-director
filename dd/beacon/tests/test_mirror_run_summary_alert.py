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

"""Unit tests for a completed run's failure-alert escalation (``_log_run_summary``).

``health_logger`` only reaches the Discord alerts channel at ERROR/CRITICAL, so a run
that finishes with failed targets must escalate: any failure is an ERROR, becoming
CRITICAL once failures reach whichever is larger of the flat floor or the ratio of the
run's targets.
"""

import logging
from time import perf_counter
from unittest.mock import MagicMock

import pytest

from dd.beacon.extensions import mirror
from dd.beacon.mirror_core import MirrorOperationType, RunCounts, RunView


def _view(delivered: int, failed: int, cancelled: int = 0) -> RunView:
    view = RunView(
        op=MirrorOperationType.SEND,
        src_ch_id=1,
        src_msg_id=99,
        start_time=perf_counter(),
    )
    view.counts = RunCounts(delivered=delivered, failed=failed, cancelled=cancelled)
    return view


@pytest.mark.parametrize(
    ("delivered", "failed", "expected_level"),
    [
        (100, 0, None),  # no failures → no alert at all
        (97, 3, logging.ERROR),  # 3/100 < max(10, 10%*100=10) → ERROR
        (90, 10, logging.CRITICAL),  # 10/100 >= 10 → CRITICAL
        (35, 5, logging.ERROR),  # 5/40: max(10, ceil(4))=10, 5 < 10 → ERROR
        (440, 60, logging.CRITICAL),  # 60/500: max(10, 50)=50, 60 >= 50 → CRITICAL
        (0, 2, logging.ERROR),  # tiny run: max(10, ceil(0.2))=10, 2 < 10 → ERROR
    ],
)
def test_run_summary_failure_escalation(
    monkeypatch: pytest.MonkeyPatch,
    delivered: int,
    failed: int,
    expected_level: int | None,
) -> None:
    health = MagicMock()
    monkeypatch.setattr(mirror, "health_logger", health)

    mirror._log_run_summary(_view(delivered, failed))

    if expected_level is None:
        health.log.assert_not_called()
    else:
        health.log.assert_called_once()
        assert health.log.call_args.args[0] == expected_level
