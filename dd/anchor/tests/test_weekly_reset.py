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
            ("Null Composure", 222, "Fusion Rifle", 3, "Legendary"),
            ("Cloudstrike", 333, "Sniper Rifle", 3, "Exotic"),
            ("Chill Inhibitor", 444, "Grenade Launcher", 3, "Exotic"),
        ],
        activities={
            "raid": ["Crota's End", "Vault of Glass"],
            "dungeon": ["Duality"],
            "strike": ["The Sunless Cell"],
            "pantheon": ["Argos", "Calus"],
            "crucible": ["Control"],
        },
    )
    yield
    wr._indexes = saved


def test_choice_selector_domains_fit_discord_limit() -> None:
    # The Choice-selector fields must stay under Discord's 25-choice limit.
    for domain in (wr.RAIDS, wr.DUNGEONS, wr.PANTHEON_BOSSES):
        assert 0 < len(domain) < 25, len(domain)
    # Crucible exceeds 25 (base + Labs), which is why it uses autocomplete instead.
    assert len(wr.CRUCIBLE_MODES) > 25
    assert "Heavy Metal Supremacy" in wr.CRUCIBLE_MODES
    # no duplicate choices anywhere
    for domain in (wr.CRUCIBLE_MODES, wr.RAIDS, wr.DUNGEONS, wr.PANTHEON_BOSSES):
        assert len(set(domain)) == len(domain)


def test_seasonal_defaults() -> None:
    fresh = wr.WeeklyResetContext(reset_ts=1)
    assert fresh.seasonal_raid == "The Desert Perpetual"
    assert fresh.seasonal_dungeon == "Equilibrium"
    config = wr.WeeklyResetConfig()
    assert config.seasonal_raid == "The Desert Perpetual"
    assert config.seasonal_dungeon == "Equilibrium"


def test_activity_record_is_flat_and_parseable() -> None:
    ctx = _full_ctx()
    rec = wr.activity_record(ctx)
    assert rec["reset_ts"] == 1783443600
    assert rec["gm_weapon"] == "Null Composure"  # WeaponRef -> name
    assert rec["rotator_raids"] == ["Crota's End", "Vault of Glass"]
    assert rec["seasonal_raid"] == "The Desert Perpetual"
    # every value must be JSON-serialisable
    import json

    assert json.loads(json.dumps(rec)) == rec


def test_reward_fields_are_weapons_only() -> None:
    for _label, key in wr._REWARD_FIELDS:
        assert wr._REWARD_ITEM_TYPE[key] == 3


@pytest.mark.parametrize(
    ("defn", "type_name", "expected"),
    [
        # Pantheon reprise/encore encounters -> pantheon (before the raid-mode check)
        (
            {
                "displayProperties": {"name": "Featured Reprise: Calus: The Pantheon"},
                "activityModeTypes": [4],
            },
            "Raid",
            "pantheon",
        ),
        # other Pantheon-named activities are excluded from every pool
        (
            {
                "displayProperties": {"name": "The Pantheon: Atraks Sovereign"},
                "activityModeTypes": [4],
            },
            "Raid",
            None,
        ),
        # authoritative type name
        ({}, "Raid", "raid"),
        ({}, "Dungeon", "dungeon"),
        ({}, "Strike", "strike"),
        # battlegrounds feed the GM strike pool by name
        (
            {"displayProperties": {"name": "Defiant Battleground: EDZ"}},
            "Nightfall",
            "strike",
        ),
        # mode fallback when no type
        ({"activityModeTypes": [4]}, "", "raid"),
        ({"directActivityModeType": 82}, "", "dungeon"),
        # fireteam-size fallback only when there is no type AND no mode
        ({"matchmaking": {"maxParty": 6}}, "", "raid"),
        ({"matchmaking": {"maxParty": 3}}, "", "dungeon"),
        # a typed 3-player strike is a strike, never a dungeon
        ({"matchmaking": {"maxParty": 3}}, "Strike", "strike"),
        ({"matchmaking": {"maxParty": 4}}, "", None),
        ({}, "", None),
    ],
)
def test_classify_activity(defn: dict, type_name: str, expected: str | None) -> None:
    assert wr._classify_activity(defn, type_name) == expected


def test_strip_variant() -> None:
    assert wr._strip_variant("Ghosts of the Deep: Standard") == "Ghosts of the Deep"
    assert wr._strip_variant("Vault of Glass: Challenge Mode") == "Vault of Glass"
    assert wr._strip_variant("Last Wish: Level 58") == "Last Wish"
    assert (
        wr._strip_variant("The Desert Perpetual (Epic): Standard")
        == "The Desert Perpetual"
    )
    # a meaningful ": X" (battleground location) is preserved
    assert wr._strip_variant("Defiant Battleground: EDZ") == "Defiant Battleground: EDZ"


def test_clean_activity_name() -> None:
    assert wr._clean_activity_name("Crota's End: Standard", "raid") == "Crota's End"
    assert wr._clean_activity_name("Grandmaster", "raid") == ""  # difficulty-only
    # strike playlist/event junk dropped
    assert (
        wr._clean_activity_name("Guardian Games: Competitive Nightfall", "strike") == ""
    )
    assert wr._clean_activity_name("The Sunless Cell", "strike") == "The Sunless Cell"
    # pantheon boss extracted from the Featured Reprise/Encore encounter names
    assert (
        wr._clean_activity_name("Featured Reprise: Calus: The Pantheon", "pantheon")
        == "Calus"
    )
    assert (
        wr._clean_activity_name(
            "Featured Encore: Warpriest: Atraks Sovereign", "pantheon"
        )
        == "Warpriest"
    )
    assert wr._clean_activity_name("The Pantheon: Atraks Sovereign", "pantheon") == ""


def test_apply_crucible_fixes_first_mode() -> None:
    ctx = wr.WeeklyResetContext(reset_ts=1)
    wr.apply_crucible(ctx, "Clash", "Rift")
    assert ctx.crucible_3v3 == "Competitive, Clash"
    assert ctx.crucible_6v6 == "Control, Rift"
    # only-one-provided leaves the other untouched
    wr.apply_crucible(ctx, "Eruption", "")
    assert ctx.crucible_3v3 == "Competitive, Eruption"
    assert ctx.crucible_6v6 == "Control, Rift"


def test_apply_pantheon_raids_dungeons() -> None:
    ctx = wr.WeeklyResetContext(reset_ts=1)
    wr.apply_pantheon(ctx, "Argos", "Calus")
    assert (ctx.pantheon_reprise, ctx.pantheon_encore) == ("Argos", "Calus")
    wr.apply_raids(ctx, "The Desert Perpetual", "Vault of Glass", "Crota's End")
    assert ctx.seasonal_raid == "The Desert Perpetual"
    assert ctx.rotator_raids == ("Vault of Glass", "Crota's End")
    wr.apply_dungeons(ctx, "Equilibrium", "Duality", "Prophecy")
    assert ctx.seasonal_dungeon == "Equilibrium"
    assert ctx.rotator_dungeons == ("Duality", "Prophecy")
    wr.apply_gm_strike(ctx, "The Sunless Cell")
    assert ctx.gm_strike == "The Sunless Cell"


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
