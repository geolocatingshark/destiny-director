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

"""Pure-logic tests for command-usage tracking + the stats chart helpers (no DB)."""

import datetime as dt

import hikari as h

from dd.beacon.extensions.statistics import (
    _bar,
    _build_command_chart,
    _build_totals_chart,
    _delta,
    _should_track,
    _sparkline,
)


def test_tracks_user_facing_slash_commands():
    assert _should_track("xur", h.CommandType.SLASH)
    assert _should_track("lost sector", h.CommandType.SLASH)
    # Admin-created custom user-commands have arbitrary top-level names → tracked.
    assert _should_track("my_custom_cmd", h.CommandType.SLASH)


def test_excludes_owner_admin_groups():
    assert not _should_track("autopost xur", h.CommandType.SLASH)
    assert not _should_track("stats commands", h.CommandType.SLASH)
    assert not _should_track("mirror manual_add", h.CommandType.SLASH)
    assert not _should_track("testing storm", h.CommandType.SLASH)
    assert not _should_track("command preview", h.CommandType.SLASH)


def test_excludes_non_slash_commands():
    assert not _should_track("Edit", h.CommandType.MESSAGE)
    assert not _should_track("Some User Menu", h.CommandType.USER)


# -- text-chart helpers ------------------------------------------------------


def test_bar_proportional_and_clamped():
    assert _bar(10, 10, width=10) == "█" * 10
    assert _bar(0, 10, width=10) == "░" * 10
    assert _bar(5, 10, width=10) == "█" * 5 + "░" * 5
    # any positive value shows at least one filled cell
    assert _bar(1, 1000, width=10).startswith("█")
    # zero max -> empty bar, no division error
    assert _bar(3, 0, width=4) == "░" * 4


def test_sparkline_levels_and_baseline():
    assert _sparkline([0, 0, 0], width=3) == "▁▁▁"
    spark = _sparkline([0, 8], width=2)
    assert spark[0] == "▁" and spark[-1] == "█"
    # a longer-than-width series is downsampled to exactly the width
    assert len(_sparkline(list(range(30)), width=7)) == 7


def test_delta_directions():
    assert _delta(127, 100) == "↑27%"
    assert _delta(92, 100) == "↓8%"
    assert _delta(100, 100) == "→0%"
    assert _delta(5, 0) == "new"
    assert _delta(0, 0) == "—"


def test_build_command_chart_ranks_and_marks_trend():
    today = dt.date(2026, 6, 26)
    window = 7
    cur = today  # in the current window
    prev = today - dt.timedelta(days=window)  # in the previous window
    rows = [
        ("xur", cur, 10),  # 10 now vs 5 before -> up, ranks first
        ("xur", prev, 5),
        ("ada", cur, 4),  # 4 now vs 8 before -> down
        ("ada", prev, 8),
        ("brand_new", cur, 2),  # only current data -> "new"
    ]

    lines = _build_command_chart(rows, today=today, window_days=window).split("\n")

    assert lines[0].startswith("/xur")  # highest current first
    assert "↑" in lines[0]
    assert "↓" in next(line for line in lines if line.startswith("/ada"))
    assert "new" in next(line for line in lines if line.startswith("/brand_new"))


def test_build_command_chart_top_n_and_empty():
    today = dt.date(2026, 6, 26)
    rows = [(f"c{i:02d}", today, i + 1) for i in range(20)]
    chart = _build_command_chart(rows, today=today, window_days=7, top_n=5)
    assert len(chart.split("\n")) == 5
    assert _build_command_chart([], today=today, window_days=7) == ""


def test_build_command_chart_truncates_long_names():
    today = dt.date(2026, 6, 26)
    rows = [("a_very_long_command_name", today, 3)]
    assert "…" in _build_command_chart(rows, today=today, window_days=7)


def test_build_totals_chart_excludes_zero_and_ranks():
    lines = _build_totals_chart([("xur", 100), ("ada", 40), ("zero", 0)]).split("\n")
    assert len(lines) == 2  # zero-count excluded
    assert lines[0].startswith("/xur")
    assert "█" in lines[0]
