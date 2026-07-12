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


@pytest.fixture(autouse=True)
def _reset_dedup() -> None:
    # The failure-alert dedup is module-level state; keep tests independent.
    mirror._last_alerted_failure.clear()


def _view(
    delivered: int,
    failed: int,
    cancelled: int = 0,
    *,
    src_msg_id: int = 99,
    op: MirrorOperationType = MirrorOperationType.SEND,
) -> RunView:
    view = RunView(
        op=op,
        src_ch_id=1,
        src_msg_id=src_msg_id,
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


def test_repeated_failure_set_for_a_source_pages_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The bug: a send run and an edit run for one source both observe the same 50/200
    # failed (the edit re-arms and re-fails the same targets) and both paged. Now only
    # the first pages; the re-run (a different op label, same source + blast radius) is
    # deduped.
    health = MagicMock()
    monkeypatch.setattr(mirror, "health_logger", health)

    mirror._log_run_summary(_view(150, 50, op=MirrorOperationType.SEND))
    mirror._log_run_summary(_view(150, 50, op=MirrorOperationType.UPDATE))

    health.log.assert_called_once()  # the update re-run did not re-page


def test_different_sources_page_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health = MagicMock()
    monkeypatch.setattr(mirror, "health_logger", health)

    mirror._log_run_summary(_view(150, 50, src_msg_id=1))
    mirror._log_run_summary(_view(150, 50, src_msg_id=2))

    assert health.log.call_count == 2  # shared failure set not deduped across sources


def test_changed_blast_radius_repages(monkeypatch: pytest.MonkeyPatch) -> None:
    health = MagicMock()
    monkeypatch.setattr(mirror, "health_logger", health)

    mirror._log_run_summary(_view(150, 50))  # 50/200 → page
    mirror._log_run_summary(_view(140, 60))  # 60/200 → different radius → re-page

    assert health.log.call_count == 2


def test_clean_run_resets_dedup_so_a_recurrence_repages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health = MagicMock()
    monkeypatch.setattr(mirror, "health_logger", health)

    mirror._log_run_summary(_view(150, 50))  # page
    mirror._log_run_summary(_view(150, 50))  # deduped
    mirror._log_run_summary(_view(200, 0))  # fully resolved → clears the dedup entry
    mirror._log_run_summary(_view(150, 50))  # recurrence → pages again

    assert health.log.call_count == 2
