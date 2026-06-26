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
and reward-emoji mapping. No DB / network / Discord — the live data path
(``fetch_portal_ops``) is not exercised here."""

from dd.anchor.extensions.portal_ops import (
    MODE_CRUCIBLE,
    MODE_GAMBIT,
    MODE_IRON_BANNER,
    MODE_RACING,
    MODE_TRIALS,
    PortalOp,
    _collect_activities_by_hash,
    _guaranteed_reward_hash,
    _is_guaranteed_reward_style,
    _reward_emoji,
    base_activity_name,
    bucket_for,
    dedupe_ops,
    ops_by_tab,
)


def _op(
    *,
    tab="Fireteam Ops",
    activity_name="Quickplay",
    activity_type="Mission",
    reward_name="Some Gun",
    reward_hash=1,
    reward_emoji=":weapon:",
    tier=2,
) -> PortalOp:
    return PortalOp(
        tab=tab,
        activity_name=activity_name,
        activity_type=activity_type,
        reward_name=reward_name,
        reward_hash=reward_hash,
        reward_emoji=reward_emoji,
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


def test_bucket_for_sparrow_racing_goes_to_crucible():
    # Sparrow Racing League (mode 94) is grouped under the Crucible tab; its activity
    # type name would otherwise fall through to Fireteam Ops by party size.
    assert bucket_for("Sparrow Racing League", MODE_RACING, 6) == "Crucible"


def test_bucket_for_pve_ops_tabs_by_activity_type():
    # The four PvE Ops tabs are identified by the activity *type* name (verified live
    # against the in-game Portal), not by fireteam size.
    assert bucket_for("Solo Ops", 3, 1) == "Solo Ops"
    assert bucket_for("Mission", 3, 3) == "Fireteam Ops"
    # Pinnacle Ops formats (exotic missions, Crawl, Onslaught) — maxParty > 1 used to
    # mislabel these as Fireteam Ops.
    assert bucket_for("Exotic Mission", 3, 3) == "Pinnacle Ops"
    assert bucket_for("Crawl", 3, 3) == "Pinnacle Ops"
    assert bucket_for("Onslaught", 86, 3) == "Pinnacle Ops"
    # Seasonal Arena is an Arena Op.
    assert bucket_for("Seasonal Arena", None, 6) == "Arena Ops"


def test_bucket_for_vanguard_op_split_by_fireteam_size():
    # "Vanguard Op" covers both the Fireteam and Arena "Vanguard Alert" playlists; the
    # 6-player Arena variant is told apart by maxParty.
    assert bucket_for("Vanguard Op", 3, 3) == "Fireteam Ops"
    assert bucket_for("Vanguard Op", 3, 6) == "Arena Ops"


def test_bucket_for_unknown_pve_type_falls_back_to_party_size():
    assert bucket_for("Some Future Type", 3, 1) == "Solo Ops"
    assert bucket_for("Some Future Type", 3, 3) == "Fireteam Ops"


def test_bucket_for_pvp_by_type_name_when_mode_absent():
    # A PvP type without a mapped modeType still buckets by type name.
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


# ── guaranteed-reward extraction ───────────────────────────────────────────────


def _activity(*reward_items, **extra):
    """A focused-activity dict with one visibleReward block. ``reward_items`` are
    ``(uiStyle, itemHash)`` pairs."""
    return {
        "visibleRewards": [
            {
                "rewardItems": [
                    {"uiStyle": style, "itemQuantity": {"itemHash": hash_}}
                    for style, hash_ in reward_items
                ]
            }
        ],
        **extra,
    }


def test_is_guaranteed_reward_style_matches_guaranteed_family():
    # Daily, weekly and seasonal grind ops all carry a ``…_guaranteed`` suffix; the
    # generic bonus engram and empty/missing styles do not.
    assert _is_guaranteed_reward_style("daily_grind_guaranteed")
    assert _is_guaranteed_reward_style("weekly_grind_guaranteed")
    assert not _is_guaranteed_reward_style("extra_engram")
    assert not _is_guaranteed_reward_style("")
    assert not _is_guaranteed_reward_style(None)


def test_guaranteed_reward_hash_picks_guaranteed_over_bonus_engram():
    activity = _activity(("extra_engram", 50), ("daily_grind_guaranteed", 77))
    assert _guaranteed_reward_hash([activity]) == 77


def test_guaranteed_reward_hash_resolves_across_character_copies():
    # The first character's copy (already claimed today) lacks the guaranteed marker;
    # a later copy still exposes it. The reward must be found across copies, else the
    # op is silently dropped from the post.
    without = _activity(("extra_engram", 50))
    with_reward = _activity(("weekly_grind_guaranteed", 77))
    assert _guaranteed_reward_hash([without, with_reward]) == 77


def test_guaranteed_reward_hash_none_when_no_guaranteed_drop():
    assert _guaranteed_reward_hash([_activity(("extra_engram", 50))]) is None
    assert _guaranteed_reward_hash([]) is None


# ── _collect_activities_by_hash ────────────────────────────────────────────────


def test_collect_activities_by_hash_groups_every_copy():
    # No isFocusedActivity filter: featured ops are selected downstream by their
    # guaranteed-reward marker, so collection keeps every available activity (and every
    # character's copy, so the reward can be resolved across copies).
    character_activities = {
        "char1": {
            "availableActivities": [
                {"activityHash": 1, "isFocusedActivity": True},
                {"activityHash": 2, "isFocusedActivity": False},
            ]
        },
        "char2": {
            "availableActivities": [
                {"activityHash": 1, "isFocusedActivity": True},
            ]
        },
    }
    activities = _collect_activities_by_hash(character_activities)
    # Both hashes kept (focused flag is ignored); hash 1 retains both copies.
    assert set(activities) == {1, 2}
    assert len(activities[1]) == 2
    assert len(activities[2]) == 1


# ── _reward_emoji ──────────────────────────────────────────────────────────────


def test_reward_emoji_maps_weapon_armor_and_fallback():
    # A weapon type with a matching server emoji → that specific emoji.
    assert (
        _reward_emoji({"itemType": 3, "itemTypeDisplayName": "Hand Cannon"})
        == ":hand_cannon:"
    )
    assert (
        _reward_emoji({"itemType": 3, "itemTypeDisplayName": "Sniper Rifle"})
        == ":sniper_rifle:"
    )
    # Bows map to the combat_bow emoji.
    assert (
        _reward_emoji({"itemType": 3, "itemTypeDisplayName": "Combat Bow"})
        == ":combat_bow:"
    )
    # Armor (itemType 2) → :armor:.
    assert _reward_emoji({"itemType": 2, "itemTypeDisplayName": "Helmet"}) == ":armor:"
    # Unknown weapon type / missing def → generic :weapon:.
    assert _reward_emoji({"itemType": 3, "itemTypeDisplayName": ""}) == ":weapon:"
    assert _reward_emoji(None) == ":weapon:"
