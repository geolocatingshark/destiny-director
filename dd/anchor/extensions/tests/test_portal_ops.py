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

"""Pure-helper tests for Portal Ops: name normalisation, bucketing, dedup, grouping,
and the Pinnacle fixed-rotation indexing. No DB / network / Discord — the live data
path (``fetch_portal_ops``) is not exercised here."""

import datetime as dt

from dd.anchor.extensions.portal_ops import (
    MODE_CRUCIBLE,
    MODE_GAMBIT,
    MODE_IRON_BANNER,
    MODE_TRIALS,
    PortalOp,
    base_activity_name,
    bucket_for,
    current_pinnacle_op,
    dedupe_ops,
    ops_by_tab,
    pinnacle_rotation_index,
)


def _op(
    *,
    tab="Fireteam Ops",
    activity_name="Quickplay",
    activity_type="Mission",
    reward_name="Some Gun",
    reward_hash=1,
    tier=2,
) -> PortalOp:
    return PortalOp(
        tab=tab,
        activity_name=activity_name,
        activity_type=activity_type,
        reward_name=reward_name,
        reward_hash=reward_hash,
        tier=tier,
    )


# ── base_activity_name ─────────────────────────────────────────────────────────


def test_base_activity_name_strips_variant_suffixes():
    assert base_activity_name("The Disgraced: Matchmade") == "The Disgraced"
    assert base_activity_name("Proving Grounds: Customize") == "Proving Grounds"
    assert base_activity_name("Quickplay: Normal") == "Quickplay"
    assert base_activity_name("Quickplay: Master") == "Quickplay"


def test_base_activity_name_keeps_unsuffixed_and_inner_colons():
    assert base_activity_name("Lake of Shadows") == "Lake of Shadows"
    # An inner colon that is not a known variant suffix is preserved.
    assert base_activity_name("Override: The Moon") == "Override: The Moon"


# ── bucket_for ─────────────────────────────────────────────────────────────────


def test_bucket_for_pvp_modes_take_precedence():
    assert bucket_for("Gambit", MODE_GAMBIT, 4) == "Gambit"
    assert bucket_for("Trials of Osiris", MODE_TRIALS, 3) == "Trials"
    assert bucket_for("Iron Banner", MODE_IRON_BANNER, 6) == "Iron Banner"
    assert bucket_for("The Crucible", MODE_CRUCIBLE, 3) == "Crucible"


def test_bucket_for_pve_solo_vs_fireteam():
    assert bucket_for("Solo Ops", 3, 1) == "Solo Ops"
    assert bucket_for("Mission", 3, 3) == "Fireteam Ops"
    assert bucket_for("Onslaught", 86, 3) == "Fireteam Ops"
    # maxParty drives the solo/fireteam split even with a non-solo type name.
    assert bucket_for("Vanguard Op", 3, 1) == "Solo Ops"


def test_bucket_for_pvp_by_type_name_when_mode_absent():
    # Seasonal Arena PvP without a mapped modeType still buckets by type name.
    assert bucket_for("Crucible Rumble", None, 6) == "Crucible"


# ── dedupe_ops ─────────────────────────────────────────────────────────────────


def test_dedupe_collapses_matchmade_and_customize_pairs():
    ops = [
        _op(activity_name="The Disgraced", reward_hash=100),
        _op(activity_name="The Disgraced", reward_hash=100),
    ]
    assert len(dedupe_ops(ops)) == 1


def test_dedupe_keeps_same_name_with_different_reward():
    # A Solo "Quickplay" and a Fireteam "Quickplay" with different rewards are
    # distinct ops and must both survive.
    ops = [
        _op(tab="Solo Ops", activity_name="Quickplay", reward_hash=1),
        _op(tab="Fireteam Ops", activity_name="Quickplay", reward_hash=2),
    ]
    assert len(dedupe_ops(ops)) == 2


def test_dedupe_keeps_same_reward_with_different_activity():
    ops = [
        _op(activity_name="Lake of Shadows", reward_hash=5),
        _op(activity_name="The Glassway", reward_hash=5),
    ]
    assert len(dedupe_ops(ops)) == 2


# ── ops_by_tab ─────────────────────────────────────────────────────────────────


def test_ops_by_tab_orders_tabs_and_sorts_within():
    ops = [
        _op(tab="Gambit", activity_name="Gambit"),
        _op(tab="Solo Ops", activity_name="The Conflux"),
        _op(tab="Solo Ops", activity_name="Quickplay"),
        _op(tab="Fireteam Ops", activity_name="Midtown"),
    ]
    grouped = ops_by_tab(ops)
    # Solo Ops before Fireteam Ops before Gambit (TAB_ORDER).
    assert list(grouped.keys()) == ["Solo Ops", "Fireteam Ops", "Gambit"]
    # Within Solo Ops, sorted by activity name.
    assert [o.activity_name for o in grouped["Solo Ops"]] == [
        "Quickplay",
        "The Conflux",
    ]


# ── Pinnacle fixed rotation ────────────────────────────────────────────────────

_REF = dt.datetime(2025, 7, 15, 17, tzinfo=dt.UTC)
_ROTATION = [["Raid A"], ["Dungeon B"], ["GM C"]]


def test_pinnacle_rotation_index_advances_weekly():
    week = dt.timedelta(days=7)
    assert pinnacle_rotation_index(_REF, reference_date=_REF, rotation_len=3) == 0
    assert (
        pinnacle_rotation_index(_REF + week, reference_date=_REF, rotation_len=3) == 1
    )
    assert (
        pinnacle_rotation_index(_REF + 2 * week, reference_date=_REF, rotation_len=3)
        == 2
    )
    # Wraps around after the rotation length.
    assert (
        pinnacle_rotation_index(_REF + 3 * week, reference_date=_REF, rotation_len=3)
        == 0
    )


def test_pinnacle_rotation_index_clamps_before_reference():
    week = dt.timedelta(days=7)
    assert (
        pinnacle_rotation_index(_REF - week, reference_date=_REF, rotation_len=3) == 0
    )


def test_pinnacle_rotation_index_none_when_unseeded():
    assert pinnacle_rotation_index(_REF, reference_date=_REF, rotation_len=0) is None


def test_current_pinnacle_op_returns_seeded_rotation():
    # PINNACLE_ROTATION is seeded (dev), so the current featured Pinnacle ops are
    # non-empty and match the seeded set; the unseeded (empty) path is covered by
    # test_pinnacle_rotation_index_none_when_unseeded.
    ops = current_pinnacle_op()
    assert ops
    assert "Root of Nightmares" in ops
