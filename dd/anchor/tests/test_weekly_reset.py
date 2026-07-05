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

"""Unit tests for the ``weekly_reset`` extension's pure logic (no Discord I/O).

The interactive menu/modal flow is verified manually on dev; here we exercise the
reset-time maths, the deterministic rotator cycle (anchored to the three real posts this
was built from), the post-body renderer, (de)serialisation, validation, reconciliation
and the Components V2 builder.
"""

import datetime as dt

import hikari as h
import pytest

from dd.anchor.extensions import weekly_reset as wr

# The three real "Weekly Reset Overview" posts this feature was reverse-engineered from.
SAMPLE_RESETS = (1782234000, 1782838800, 1783443600)
SAMPLE_RAIDS = (
    ("King's Fall", "Garden of Salvation"),
    ("Root of Nightmares", "Deep Stone Crypt"),
    ("Crota's End", "Vault of Glass"),
)
SAMPLE_DUNGEONS = (
    ("Spire of the Watcher", "Pit of Heresy"),
    ("Ghosts of the Deep", "Prophecy"),
    ("Warlord's Ruin", "Grasp of Avarice"),
)


def _full_ctx() -> wr.WeeklyResetContext:
    ctx = wr.WeeklyResetContext(reset_ts=1783443600)
    ctx.quickplay_weapon = wr.WeaponRef("Service Revolver", 111)
    ctx.gm_strike = "The Sunless Cell"
    ctx.gm_weapon = wr.WeaponRef("Null Composure", 222)
    ctx.control_weapon = wr.WeaponRef("Unending Tempest", 333)
    ctx.seasonal_raid = "The Desert Perpetual"
    ctx.seasonal_dungeon = "Equilibrium"
    ctx.rotator_raids = ("Crota's End", "Vault of Glass")
    ctx.rotator_dungeons = ("Warlord's Ruin", "Grasp of Avarice")
    ctx.pantheon_reprise = "Argos"
    ctx.pantheon_encore = "Insurrection Prime"
    ctx.zavala_weapon = wr.WeaponRef("Horror's Least", 444, "pulse_rifle")
    ctx.zavala_options = [ctx.zavala_weapon, wr.WeaponRef("Something Else", 555)]
    ctx.crucible_3v3 = "Competitive, Clash"
    ctx.crucible_6v6 = "Control, Eruption"
    ctx.notes = ["Duality is available due to a bug."]
    return ctx


# --- reset-time maths -------------------------------------------------------------


@pytest.mark.parametrize("reset_ts", SAMPLE_RESETS)
def test_current_reset_ts_matches_samples(reset_ts: int) -> None:
    at_reset = dt.datetime.fromtimestamp(reset_ts, tz=dt.UTC)
    assert wr.current_reset_ts(at_reset) == reset_ts


def test_current_reset_ts_floors_midweek() -> None:
    # Thursday of the first sampled week floors back to that week's Tuesday reset.
    midweek = dt.datetime(2026, 6, 25, 20, 0, tzinfo=dt.UTC)
    assert wr.current_reset_ts(midweek) == 1782234000


# --- deterministic rotator cycle --------------------------------------------------


@pytest.mark.parametrize(
    ("reset_ts", "raids", "dungeons"),
    list(zip(SAMPLE_RESETS, SAMPLE_RAIDS, SAMPLE_DUNGEONS, strict=True)),
)
def test_rotators_reproduce_sampled_weeks(
    reset_ts: int, raids: tuple[str, str], dungeons: tuple[str, str]
) -> None:
    anchor = wr.DEFAULT_ROTATOR_ANCHOR
    assert wr.compute_rotator(wr.DEFAULT_RAID_PAIRS, anchor, reset_ts) == raids
    assert wr.compute_rotator(wr.DEFAULT_DUNGEON_PAIRS, anchor, reset_ts) == dungeons


def test_rotator_cycle_wraps() -> None:
    # One full cycle later lands back on the anchor week's raids.
    one_cycle = 1782234000 + len(wr.DEFAULT_RAID_PAIRS) * 7 * 86400
    assert (
        wr.compute_rotator(wr.DEFAULT_RAID_PAIRS, wr.DEFAULT_ROTATOR_ANCHOR, one_cycle)
        == wr.DEFAULT_RAID_PAIRS[0]
    )


# --- post-body renderer -----------------------------------------------------------


def test_build_body_has_all_sections_and_deeplink() -> None:
    body = wr.build_body(_full_ctx())
    for marker in (
        "# Weekly Reset Overview",
        "Resets: <t:1783443600:f>",
        "**VANGUARD ALERTS (Seasonal Tab)**",
        "GM Alert: The Sunless Cell",
        "**FEATURED RAIDS & DUNGEONS**",
        "Crota's End + Vault of Glass",
        "Reprise: Argos",
        "**ZAVALA'S WEAPON**",
        "**CRUCIBLE OPS**",
        ":info: Duality is available due to a bug.",
        "See you starside",
    ):
        assert marker in body, marker
    # light.gg deep link uses the item hash (fixes the old bare placeholder).
    assert "https://light.gg/db/items/444" in body


def test_iron_banner_week_hides_trials_line_shows_reminder() -> None:
    ctx = _full_ctx()
    ctx.iron_banner = True
    ctx.trials_active = False
    body = wr.build_body(ctx)
    assert "Iron Banner has returned" in body
    assert "From Friday - Tuesday" not in body  # the Trials line is gated off
    assert wr.TRIALS_IB_REMINDER in body


def test_trials_line_shows_on_non_ib_week() -> None:
    ctx = _full_ctx()
    ctx.iron_banner = False
    ctx.trials_active = True
    assert "From Friday - Tuesday" in wr.build_body(ctx)


def test_hand_typed_weapon_has_no_link() -> None:
    ctx = wr.WeeklyResetContext(reset_ts=1)
    ctx.zavala_weapon = wr.WeaponRef("Typed Name")  # no hash
    assert ctx.zavala_weapon.markdown() == "Typed Name"
    assert "](http" not in ctx.zavala_weapon.markdown()


# --- (de)serialisation ------------------------------------------------------------


def test_context_round_trip() -> None:
    ctx = _full_ctx()
    restored = wr.WeeklyResetContext.from_dict(ctx.to_dict())
    assert restored.to_dict() == ctx.to_dict()
    assert restored.rotator_raids == ("Crota's End", "Vault of Glass")
    assert restored.zavala_weapon is not None and restored.zavala_weapon.hash == 444


def test_config_round_trip_and_defaults() -> None:
    restored = wr.WeeklyResetConfig.from_dict(wr.WeeklyResetConfig().to_dict())
    assert restored.raid_pairs == wr.DEFAULT_RAID_PAIRS
    assert restored.pantheon_pool == wr.PANTHEON_BOSSES
    assert (
        wr.WeeklyResetConfig.from_dict(None).rotator_anchor == wr.DEFAULT_ROTATOR_ANCHOR
    )


# --- validation + reconciliation --------------------------------------------------


def test_validate_flags_empty_post() -> None:
    problems = wr.validate_post(wr.WeeklyResetContext(reset_ts=1))
    assert any("empty" in p for p in problems)


def test_validate_flags_bad_image_url() -> None:
    ctx = _full_ctx()
    ctx.image_url = "not-a-url"
    assert any("Image URL" in p for p in wr.validate_post(ctx))


def test_reconcile_clears_stale_pick() -> None:
    ctx = wr.WeeklyResetContext(reset_ts=1)
    ctx.zavala_options = [wr.WeaponRef("A", 1), wr.WeaponRef("B", 2)]
    ctx.zavala_weapon = wr.WeaponRef("Gone", 999)
    assert wr.reconcile_picks(ctx) == ["Zavala's Weapon"]
    assert ctx.zavala_weapon is None


def test_reconcile_keeps_valid_pick() -> None:
    ctx = wr.WeeklyResetContext(reset_ts=1)
    keep = wr.WeaponRef("A", 1)
    ctx.zavala_options = [keep, wr.WeaponRef("B", 2)]
    ctx.zavala_weapon = keep
    assert wr.reconcile_picks(ctx) == []
    assert ctx.zavala_weapon is keep


# --- editor section model + CV2 builder -------------------------------------------


def test_section_model_covers_every_section() -> None:
    ctx = _full_ctx()
    for key, _ in wr._SECTIONS:
        # None of these should raise for any section.
        wr._select_a(ctx, key)
        wr._select_b(ctx, key)
        wr._modal_spec(ctx, key)
        assert isinstance(wr._summary(ctx, key), str)


def test_modal_round_trip_notes_and_links() -> None:
    ctx = wr.WeeklyResetContext(reset_ts=1)
    wr._apply_modal(
        ctx,
        "notes",
        ["First note\nSecond note", "Guide | https://example.com\nbad line"],
    )
    assert ctx.notes == ["First note", "Second note"]
    assert ctx.extra_links == [{"label": "Guide", "url": "https://example.com"}]


def test_build_cv2_is_components_v2() -> None:
    ctx = _full_ctx()
    ctx.image_url = "https://example.com/art.jpg"
    hmessage = wr.build_cv2(wr.build_body(ctx), ctx.image_url)
    kwargs = hmessage.to_message_kwargs()
    assert kwargs["flags"] == h.MessageFlag.IS_COMPONENTS_V2
    assert kwargs["components"] and "content" not in kwargs


# --- autocomplete set commands (activities + item rewards) ------------------------


@pytest.fixture
def stub_indexes():
    saved = wr._indexes
    wr._indexes = wr._Indexes(
        items=[
            ("Null Composure", 222, "Fusion Rifle", 3),
            ("Cloudstrike", 333, "Sniper Rifle", 3),
            ("Chill Inhibitor", 444, "Grenade Launcher", 3),
        ],
        activities={
            "raid": ["Crota's End", "Vault of Glass"],
            "dungeon": ["Duality"],
            "nightfall": ["The Sunless Cell"],
            "pantheon": [],
            "crucible": ["Control"],
        },
    )
    yield
    wr._indexes = saved


def test_activity_category_maps_to_known_categories() -> None:
    valid = {"raid", "dungeon", "nightfall", "pantheon", "crucible"}
    for _label, key in wr._ACTIVITY_FIELDS:
        assert wr._ACTIVITY_CATEGORY.get(key) in valid, key


def test_reward_fields_are_weapons_only() -> None:
    for _label, key in wr._REWARD_FIELDS:
        assert wr._REWARD_ITEM_TYPE[key] == 3


@pytest.mark.parametrize(
    ("defn", "type_name", "expected"),
    [
        # authoritative type name wins
        ({}, "Raid", "raid"),
        ({}, "Dungeon", "dungeon"),
        ({}, "Nightfall", "nightfall"),
        # mode-based when no type
        ({"activityModeTypes": [4]}, "", "raid"),
        ({"directActivityModeType": 82}, "", "dungeon"),
        ({"activityModeTypes": [46, 7]}, "", "nightfall"),
        ({"displayProperties": {"name": "Pantheon: Nezarec Sublime"}}, "", "pantheon"),
        # fireteam-size fallback only when there is no type AND no mode
        ({"matchmaking": {"maxParty": 6}}, "", "raid"),
        ({"matchmaking": {"maxParty": 3}}, "", "dungeon"),
        # a typed 3-player strike must NOT be mistaken for a dungeon
        ({"matchmaking": {"maxParty": 3}}, "Strike", None),
        ({"matchmaking": {"maxParty": 4}}, "", None),
        ({}, "", None),
    ],
)
def test_classify_activity(defn: dict, type_name: str, expected: str | None) -> None:
    assert wr._classify_activity(defn, type_name) == expected


def test_clean_activity_name_variants() -> None:
    # raid/dungeon difficulty variants are dropped (the base is kept from its own row)
    assert wr._clean_activity_name("Vault of Glass: Master", "raid") == ""
    assert wr._clean_activity_name("Vault of Glass", "raid") == "Vault of Glass"
    # nightfall prefixes stripped
    assert (
        wr._clean_activity_name("Grandmaster: The Sunless Cell", "nightfall")
        == "The Sunless Cell"
    )
    assert (
        wr._clean_activity_name("Nightfall: The Corrupted", "nightfall")
        == "The Corrupted"
    )
    # pantheon prefix stripped
    assert (
        wr._clean_activity_name("Pantheon: Rhulk Indomitable", "pantheon")
        == "Rhulk Indomitable"
    )


def test_apply_activity_field_rotators_and_plain() -> None:
    ctx = wr.WeeklyResetContext(reset_ts=1)
    wr.apply_activity_field(ctx, "rotator_raid_1", "Vault of Glass")
    wr.apply_activity_field(ctx, "rotator_raid_2", "Crota's End")
    wr.apply_activity_field(ctx, "rotator_dungeon_1", "Duality")
    wr.apply_activity_field(ctx, "rotator_dungeon_2", "Prophecy")
    assert ctx.rotator_raids == ("Vault of Glass", "Crota's End")
    assert ctx.rotator_dungeons == ("Duality", "Prophecy")
    wr.apply_activity_field(ctx, "gm_strike", "The Sunless Cell")
    wr.apply_activity_field(ctx, "crucible_6v6", "Control, Eruption")
    assert ctx.gm_strike == "The Sunless Cell"
    assert ctx.crucible_6v6 == "Control, Eruption"


def test_apply_reward_field_sets_and_clears() -> None:
    ctx = wr.WeeklyResetContext(reset_ts=1)
    weapon = wr.WeaponRef("Null Composure", 222, "fusion_rifle")
    for _label, key in wr._REWARD_FIELDS:
        wr.apply_reward_field(ctx, key, weapon)
        assert getattr(ctx, key) is weapon
    wr.apply_reward_field(ctx, "zavala_weapon", None)
    assert ctx.zavala_weapon is None


@pytest.mark.asyncio
async def test_resolve_reward_by_hash(stub_indexes) -> None:
    assert await wr.resolve_reward_value("222") == wr.WeaponRef(
        "Null Composure", 222, "fusion_rifle"
    )


@pytest.mark.asyncio
async def test_resolve_reward_by_name(stub_indexes) -> None:
    weapon = await wr.resolve_reward_value("cloudstrike")
    assert weapon is not None
    assert weapon.hash == 333 and weapon.emoji_name == "sniper_rifle"


@pytest.mark.asyncio
async def test_resolve_reward_free_text_and_blank(stub_indexes) -> None:
    typed = await wr.resolve_reward_value("Some Custom Roll")
    assert typed == wr.WeaponRef(name="Some Custom Roll") and typed.hash is None
    assert await wr.resolve_reward_value("   ") is None
