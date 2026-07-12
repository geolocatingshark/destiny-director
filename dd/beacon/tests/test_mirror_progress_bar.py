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

"""Unit tests for the mirror progress bar + throughput helpers (no Discord I/O)."""

from time import perf_counter

from dd.beacon.extensions.mirror import _progress_bar, _throughput_line
from dd.beacon.mirror_core import MirrorOperationType, RunCounts, RunView
from dd.common.utils import format_duration

_GREEN, _RED, _BLANK = "🟩", "🟥", "⬜"
_BAR_WIDTH = 12


def _view(delivered=0, failed=0, cancelled=0, pending=0) -> RunView:
    view = RunView(
        op=MirrorOperationType.SEND,
        src_ch_id=1,
        src_msg_id=99,
        start_time=perf_counter(),
    )
    view.counts = RunCounts(
        delivered=delivered, failed=failed, cancelled=cancelled, pending=pending
    )
    return view


def _cells(bar: str) -> dict[str, int]:
    return {seg: bar.count(seg) for seg in (_GREEN, _RED, _BLANK)}


def _pct(bar: str) -> int:
    return int(bar.split()[-1].rstrip("%"))


# -- _progress_bar -----------------------------------------------------------


def test_bar_empty_is_all_blank() -> None:
    bar = _progress_bar(_view(pending=12))
    assert _cells(bar) == {_GREEN: 0, _RED: 0, _BLANK: _BAR_WIDTH}
    assert _pct(bar) == 0


def test_bar_all_success_is_full_green() -> None:
    bar = _progress_bar(_view(delivered=12))
    assert _cells(bar) == {_GREEN: _BAR_WIDTH, _RED: 0, _BLANK: 0}
    assert _pct(bar) == 100


def test_bar_all_permanent_failures_is_full_red_and_100pct() -> None:
    # Failures are terminal, so the resolved percentage is 100 even though nothing
    # succeeded.
    bar = _progress_bar(_view(failed=12))
    assert _cells(bar) == {_GREEN: 0, _RED: _BAR_WIDTH, _BLANK: 0}
    assert _pct(bar) == 100


def test_bar_segments_map_to_buckets_at_unit_scale() -> None:
    # 12 targets over a 12-cell bar => one cell per target, so segments equal counts.
    bar = _progress_bar(_view(delivered=6, failed=1, pending=5))
    assert _cells(bar) == {_GREEN: 6, _RED: 1, _BLANK: 5}
    # Resolved = 6 success + 1 fail = 7/12.
    assert _pct(bar) == round(7 / 12 * 100)


def test_bar_is_always_exactly_full_width() -> None:
    # Large target set with awkward ratios must never overflow or underflow the bar;
    # blanks absorb rounding and still-pending targets.
    bar = _progress_bar(_view(delivered=537, failed=97, pending=366))
    cells = _cells(bar)
    assert all(v >= 0 for v in cells.values())
    assert sum(cells.values()) == _BAR_WIDTH


def test_bar_respects_custom_width() -> None:
    bar = _progress_bar(_view(delivered=12), width=20)
    assert sum(_cells(bar).values()) == 20


# -- _throughput_line --------------------------------------------------------


def test_throughput_none_before_anything_resolves() -> None:
    assert _throughput_line(_view(pending=12), elapsed_secs=5.0) is None


def test_throughput_none_when_no_elapsed_time() -> None:
    assert _throughput_line(_view(delivered=1, pending=11), elapsed_secs=0.0) is None


def test_throughput_rate_only_when_all_resolved() -> None:
    line = _throughput_line(_view(delivered=12), elapsed_secs=6.0)
    assert line == "Throughput: 2.0 channels/sec"
    assert "ETA" not in line


def test_throughput_includes_eta_while_work_remains() -> None:
    line = _throughput_line(_view(delivered=6, pending=6), elapsed_secs=6.0)
    # 6 resolved / 6 s = 1.0 ch/s; 6 remaining => ETA of format_duration(6.0).
    assert line is not None
    assert "Throughput: 1.0 channels/sec" in line
    assert f"ETA ~{format_duration(6.0)}" in line
