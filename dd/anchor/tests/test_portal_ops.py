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

"""Unit tests for portal_ops' pure ``_build_portal_op`` (no Discord / HTTP I/O).

These guard the manifest-key mapping — a mistyped key like ``activityModeTypes`` would
otherwise only surface at runtime, since the live ``fetch_portal_ops`` path needs a
Bungie token and can't run in CI.
"""

from dd.anchor.extensions import portal_ops as po


def test_build_portal_op_maps_discriminator_fields() -> None:
    # Shaped like a live component-204 Nightfall op (The Sunless Cell → Lotus-Eater).
    activity = {"difficultyTier": 2}
    activity_def = {
        "activityTypeHash": 556925641,  # "Strike"
        "matchmaking": {"maxParty": 6},
        "directActivityModeType": 3,
        "activityModeTypes": [3, 18, 7],
        "challenges": [{"objectiveHash": 1}],
        "displayProperties": {"name": "The Sunless Cell: Customize"},
    }
    type_def = {"displayProperties": {"name": "Strike"}}
    reward_def = {
        "displayProperties": {"name": "Lotus-Eater"},
        "itemType": 3,  # weapon
        "itemTypeDisplayName": "Sidearm",
    }

    op = po._build_portal_op(activity, activity_def, type_def, reward_def, 837298567)

    assert op.activity_name == "The Sunless Cell"  # ": Customize" suffix stripped
    assert op.activity_type == "Strike"
    assert op.reward_name == "Lotus-Eater"
    assert op.reward_hash == 837298567
    assert op.tier == 2
    # Discriminators the weekly-reset derivations classify ops by:
    assert op.reward_item_type == 3
    assert op.activity_type_hash == 556925641
    assert op.challenge_count == 1
    assert op.max_party == 6
    assert op.mode_types == (3, 18, 7)


def test_build_portal_op_defaults_when_fields_absent() -> None:
    # An armour reward with no challenges / modes / matchmaking still builds cleanly.
    op = po._build_portal_op(
        {},
        {"activityTypeHash": 1, "displayProperties": {"name": "Quickplay"}},
        None,
        {"displayProperties": {"name": "Luminopotent Cuirass"}, "itemType": 2},
        123,
    )
    assert op.reward_item_type == 2  # armour
    assert op.challenge_count == 0
    assert op.mode_types == ()
    assert op.max_party is None
    assert op.tier is None
