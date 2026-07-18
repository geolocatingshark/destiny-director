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

"""Unit tests for the Iron Banner domain model + layout (no Discord/manifest I/O).

Covers the ``iron_banner`` JSON schema (validator accept/reject + the seeded default
doc), :class:`IronBannerRotation` parsing and its hard gates, the date-anchored
``active_event`` / ``current_or_next`` boundary maths (the 17:00 UTC Tuesday reset), and
the ``build_body`` post layout.
"""

import datetime as dt
import typing as t

import fastjsonschema
import pytest

from dd.common import (
    iron_banner as ib,
    rotation_schema as rs,
)

# A tiny two-event schedule with distinct pools, for deterministic boundary tests.
_DOC: dict[str, t.Any] = {
    "version": 1,
    "schedule": [
        {"start": "2026-06-30", "pool": "Pool 1", "modes": "Control / Eruption"},
        {"start": "2026-07-28", "pool": "Pool 2"},  # modes omitted -> default
    ],
    "pools": [
        {"name": "Pool 1", "weapons": ["The Forward Path", "Felwinter's Lie"]},
        {"name": "Pool 2", "weapons": ["Multimach CCX"]},
    ],
}

# 2026-06-30 17:00 UTC (the first event's start reset) and +7 days (its end).
_E1_START = int(dt.datetime(2026, 6, 30, 17, tzinfo=dt.UTC).timestamp())
_E1_END = _E1_START + 7 * 86400


# --- schema ----------------------------------------------------------------------


def test_default_doc_validates_and_is_shaped() -> None:
    doc = rs.iron_banner_default_doc()
    rs.validate("iron_banner", doc)
    assert [p["name"] for p in doc["pools"]] == ["Pool 1", "Pool 2"]
    assert len(doc["schedule"]) == len(rs.IRON_BANNER_DEFAULT_SCHEDULE)
    # Pools alternate across the seeded schedule.
    assert doc["schedule"][0]["pool"] == "Pool 1"
    assert doc["schedule"][1]["pool"] == "Pool 2"


def test_registered_but_not_a_world_activity() -> None:
    # It appears at /rotation (in ROTATION_SCHEMAS) but is date-anchored per event, so
    # it must stay out of the world-activity set (no item_links baking / seed reset).
    assert "iron_banner" in rs.ROTATION_SCHEMAS
    assert not rs.is_world_activity("iron_banner")


@pytest.mark.parametrize(
    "doc",
    [
        {"version": 1, "pools": []},  # missing schedule
        {"version": 1, "schedule": []},  # missing pools
        # a schedule entry missing its required start / pool
        {"version": 1, "schedule": [{"pool": "Pool 1"}], "pools": []},
        # additionalProperties: false on a schedule entry
        {
            "version": 1,
            "schedule": [{"start": "2026-06-30", "pool": "Pool 1", "x": 1}],
            "pools": [{"name": "Pool 1", "weapons": []}],
        },
    ],
)
def test_invalid_documents_rejected(doc: dict[str, t.Any]) -> None:
    with pytest.raises(fastjsonschema.JsonSchemaException):
        rs.validate("iron_banner", doc)


# --- domain parsing + hard gates -------------------------------------------------


def test_from_json_parses_events_ordered() -> None:
    rot = ib.IronBannerRotation.from_json(_DOC)
    assert [e.pool_name for e in rot.events] == ["Pool 1", "Pool 2"]
    first = rot.events[0]
    assert first.start_ts == _E1_START
    assert first.end_ts == _E1_END
    assert first.modes == ["Control", "Eruption"]
    assert first.pool_weapon_names == ["The Forward Path", "Felwinter's Lie"]
    # An entry that omits modes falls back to the default modes.
    assert rot.events[1].modes == ["Control", "Eruption"]


def test_from_json_rejects_undefined_pool() -> None:
    with pytest.raises(ValueError, match="undefined pool"):
        ib.IronBannerRotation.from_json(
            {
                "pools": [{"name": "Pool 1", "weapons": []}],
                "schedule": [{"start": "2026-06-30", "pool": "Ghost"}],
            }
        )


def test_from_json_rejects_bad_date() -> None:
    with pytest.raises(ValueError, match="invalid start date"):
        ib.IronBannerRotation.from_json(
            {
                "pools": [{"name": "Pool 1", "weapons": []}],
                "schedule": [{"start": "not-a-date", "pool": "Pool 1"}],
            }
        )


# --- date-anchored windowing -----------------------------------------------------


def test_active_event_within_window() -> None:
    rot = ib.IronBannerRotation.from_json(_DOC)
    mid = dt.datetime(2026, 7, 2, tzinfo=dt.UTC)
    active = rot.active_event(mid)
    assert active is not None and active.pool_name == "Pool 1"


def test_active_event_is_none_off_week() -> None:
    rot = ib.IronBannerRotation.from_json(_DOC)
    # 2026-07-15 sits between the two Iron Banner weeks (a Trials week) — nothing live.
    assert rot.active_event(dt.datetime(2026, 7, 15, tzinfo=dt.UTC)) is None


def test_boundaries_are_start_inclusive_end_exclusive() -> None:
    rot = ib.IronBannerRotation.from_json(_DOC)
    at_start = dt.datetime.fromtimestamp(_E1_START, dt.UTC)
    at_end = dt.datetime.fromtimestamp(_E1_END, dt.UTC)
    assert rot.active_event(at_start) is not None  # start is inclusive
    # The end instant is the *next* Tuesday reset — the event is over (exclusive).
    assert rot.active_event(at_end) is None
    just_before_start = at_start - dt.timedelta(seconds=1)
    assert rot.active_event(just_before_start) is None


def test_current_or_next_picks_upcoming_when_idle() -> None:
    rot = ib.IronBannerRotation.from_json(_DOC)
    before_season = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
    nxt = rot.current_or_next(before_season)
    assert nxt is not None and nxt.pool_name == "Pool 1"
    # After the whole schedule is in the past there is nothing to show.
    assert rot.current_or_next(dt.datetime(2027, 1, 1, tzinfo=dt.UTC)) is None


def test_current_or_next_prefers_active() -> None:
    rot = ib.IronBannerRotation.from_json(_DOC)
    mid = dt.datetime(2026, 7, 2, tzinfo=dt.UTC)
    assert rot.current_or_next(mid) is rot.active_event(mid)


# --- layout ----------------------------------------------------------------------


def test_build_body_layout() -> None:
    rot = ib.IronBannerRotation.from_json(_DOC)
    event = rot.events[0]
    pool_lines = [
        ":auto_rifle: [The Forward Path](https://light.gg/db/items/1)",
        ":shotgun: [Felwinter's Lie](https://light.gg/db/items/2)",
    ]
    lines = ib.build_body(event, pool_lines).split("\n")
    assert lines[0] == f"# [Iron Banner]({ib.GUIDE_URL})"
    assert f"<t:{event.start_ts}:D> – <t:{event.end_ts}:D>" in lines
    assert f"Live until <t:{event.end_ts}:f>" in lines
    assert "### Game Modes" in lines
    assert "- Control" in lines and "- Eruption" in lines
    assert "### Bonus Focus Pool" in lines
    assert ":auto_rifle: [The Forward Path](https://light.gg/db/items/1)" in lines
    # Footer links the guide + support (small text).
    assert (
        lines[-1]
        == f"-# [Iron Banner Guide]({ib.GUIDE_URL}) · [Support](https://ko-fi.com/Kyber3000)"
    )


def test_build_body_hides_empty_pool() -> None:
    rot = ib.IronBannerRotation.from_json(_DOC)
    body = ib.build_body(rot.events[0], [])
    assert "### Bonus Focus Pool" not in body
    # Modes + guide still render.
    assert "### Game Modes" in body
    assert "Iron Banner Guide" in body
