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
from dd.beacon.mirror_core import MirrorOperationType, RunFailure, RunView
from dd.common.utils import ErrorClass, format_duration

_GREEN, _YELLOW, _RED, _BLANK = "🟩", "🟨", "🟥", "⬜"
_BAR_WIDTH = 12


def _view(n_targets: int = 12) -> RunView:
    return RunView(
        op=MirrorOperationType.SEND,
        src_ch_id=1,
        src_msg_id=99,
        total=n_targets,
        start_time=perf_counter(),
    )


def _succeed(view: RunView, ch: int) -> None:
    view.on_delivered(ch)


def _fail_permanent(view: RunView, ch: int) -> None:
    view.on_failed(ch, RunFailure("PERM", ErrorClass.PERMANENT, "boom"))


def _retry(view: RunView, ch: int) -> None:
    # A single transient failure leaves the target in the "being retried" bucket:
    # attempted once, not yet resolved.
    view.on_transient(ch)


def _cells(bar: str) -> dict[str, int]:
    return {seg: bar.count(seg) for seg in (_GREEN, _YELLOW, _RED, _BLANK)}


def _pct(bar: str) -> int:
    return int(bar.split()[-1].rstrip("%"))


# -- _progress_bar -----------------------------------------------------------


def test_bar_empty_is_all_blank() -> None:
    bar = _progress_bar(_view())
    assert _cells(bar) == {_GREEN: 0, _YELLOW: 0, _RED: 0, _BLANK: _BAR_WIDTH}
    assert _pct(bar) == 0


def test_bar_all_success_is_full_green() -> None:
    view = _view()
    for ch in range(12):
        _succeed(view, ch)
    bar = _progress_bar(view)
    assert _cells(bar) == {_GREEN: _BAR_WIDTH, _YELLOW: 0, _RED: 0, _BLANK: 0}
    assert _pct(bar) == 100


def test_bar_all_permanent_failures_is_full_red_and_100pct() -> None:
    # Permanent failures are terminal, so the resolved percentage is 100 even though
    # nothing succeeded.
    view = _view()
    for ch in range(12):
        _fail_permanent(view, ch)
    bar = _progress_bar(view)
    assert _cells(bar) == {_GREEN: 0, _YELLOW: 0, _RED: _BAR_WIDTH, _BLANK: 0}
    assert _pct(bar) == 100


def test_bar_segments_map_to_buckets_at_unit_scale() -> None:
    # 12 targets over a 12-cell bar => one cell per target, so segments equal counts.
    view = _view(n_targets=12)
    for ch in range(6):
        _succeed(view, ch)
    for ch in range(6, 9):
        _retry(view, ch)
    _fail_permanent(view, 9)
    # channels 10, 11 left untried
    bar = _progress_bar(view)
    assert _cells(bar) == {_GREEN: 6, _YELLOW: 3, _RED: 1, _BLANK: 2}
    # Resolved = 6 success + 1 permanent fail = 7/12.
    assert _pct(bar) == round(7 / 12 * 100)


def test_bar_is_always_exactly_full_width() -> None:
    # Large target set with awkward ratios must never overflow or underflow the bar;
    # blanks absorb rounding and in-flight (untried) targets.
    view = _view(n_targets=1000)
    for ch in range(537):
        _succeed(view, ch)
    for ch in range(537, 537 + 211):
        _retry(view, ch)
    for ch in range(748, 748 + 97):
        _fail_permanent(view, ch)
    bar = _progress_bar(view)
    cells = _cells(bar)
    assert all(v >= 0 for v in cells.values())
    assert sum(cells.values()) == _BAR_WIDTH


def test_bar_respects_custom_width() -> None:
    view = _view()
    for ch in range(12):
        _succeed(view, ch)
    bar = _progress_bar(view, width=20)
    assert sum(_cells(bar).values()) == 20


# -- _throughput_line --------------------------------------------------------


def test_throughput_none_before_anything_resolves() -> None:
    assert _throughput_line(_view(), elapsed_secs=5.0) is None


def test_throughput_none_when_no_elapsed_time() -> None:
    view = _view()
    _succeed(view, 0)
    assert _throughput_line(view, elapsed_secs=0.0) is None


def test_throughput_rate_only_when_all_resolved() -> None:
    view = _view(n_targets=12)
    for ch in range(12):
        _succeed(view, ch)
    line = _throughput_line(view, elapsed_secs=6.0)
    assert line == "Throughput: 2.0 channels/sec"
    assert "ETA" not in line


def test_throughput_includes_eta_while_work_remains() -> None:
    view = _view(n_targets=12)
    for ch in range(6):
        _succeed(view, ch)
    line = _throughput_line(view, elapsed_secs=6.0)
    # 6 resolved / 6 s = 1.0 ch/s; 6 remaining => ETA of format_duration(6.0).
    assert line is not None
    assert "Throughput: 1.0 channels/sec" in line
    assert f"ETA ~{format_duration(6.0)}" in line
