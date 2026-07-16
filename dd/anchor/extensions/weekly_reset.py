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

"""Weekly Reset Overview — anchor producer for the ``weekly_reset`` followable.

Unlike the other followables, ``weekly_reset`` historically had *no* anchor producer:
a human hand-authored the post and dropped it into the announce channel, and beacon
mirrored it. This extension automates that authoring:

1. At Tuesday reset a cron derives everything the Bungie API can give us, merges the
   carried-over curated bits, and persists a **draft** (the WeeklyResetContext) to the
   ``weekly_reset_draft`` :class:`~dd.common.schemas.RotationData` row.
2. The team fills in the weapons/rotators/prose the API can't supply through an
   owner-authenticated **web form** (``/weekly_reset create`` links to it; the routes
   live at the bottom of this module, and Discord-OAuth auth is enforced centrally by
   the ``web_auth.py`` middleware), backed by the same data/render/publish core below.
3. On publish the assembled post is crossposted to
   :data:`cfg.followables["weekly_reset"]`; beacon mirrors it as usual.

Everything the API can't supply (editorial prose, the featured raid/dungeon rotators,
Iron Banner/Trials schedule, Pantheon pair) carries over week-to-week in the
``weekly_reset_config`` :class:`~dd.common.schemas.RotationData` row.

This module is the UI-agnostic core: the context model, the persisted config, the
Bungie derivations, the Components V2 renderer, the manifest-backed option pools, the
publish path (:func:`publish_draft`) and the reset-day autopost cron. The Discord input
UI (a `/weekly_reset` command group + interactive editor) has been removed in favour of
the web form.
"""

import asyncio
import dataclasses
import datetime as dt
import json
import logging
import re
import typing as t
from pathlib import Path

import aiocron
import aiohttp.web
import aiosqlite
import hikari as h
import lightbulb as lb

from dd.hmessage import HMessage

from ...common import cfg, schemas
from ...common.bot import CachedFetchBot
from ...common.components import (
    cv2_error,
    cv2_notice,
    guard_cv2_hmessage,
    respond_cv2,
)
from ...common.utils import fetch_emoji_dict, substitute_guild_emoji

# ``utils`` is re-exported (not used directly here now): the publish path moved to
# ``hybrid_post_core``, but the tests patch ``wr.utils`` — the same module object the
# core uses — to steer that shared publish code.
from .. import (
    hybrid_post_core,
    utils as utils,
    web,
)
from ..hybrid_post_core import (
    DraftMeta,
    HybridPostSpec,
    WeaponRef,
    # Re-exported (used only via ``wr.<name>`` in the test suite, not in this module).
    _discord_error_note as _discord_error_note,
    _format_reset_ts as _format_reset_ts,
    build_cv2,
    compute_rotator,
    current_reset_ts,
    iter_weapon_items,
    next_reset_ts,
    render_post_html as render_post_html,
    resolve_weapon,
)
from . import (
    bungie_api as api,
    portal_ops,
)

logger = logging.getLogger(__name__)
loader = lb.Loader()

# ---------------------------------------------------------------------------
# Static chrome + curated defaults
# ---------------------------------------------------------------------------

#: RotationData slug for the in-progress draft (the WeeklyResetContext being edited).
DRAFT_SLUG = "weekly_reset_draft"
#: RotationData slug for the carried-over curated config (rotator order, schedules…).
CONFIG_SLUG = "weekly_reset_config"

#: Column separator used in the post body, verbatim from the hand-authored posts.
SEP = "┊"  # ┊
LEGACY_ACTIVITIES_URL = "https://kyberscorner.com/destiny2/legacy-activities/"
SIGN_OFF = "***See you starside!*** \U0001f4ab"
#: Editorial suffix on the Zavala weapon line (tier text is not API-derivable).
ZAVALA_TIER_SUFFIX = "(T5/*rolls vary*)"
TRIALS_IB_REMINDER = (
    "Reminder: Trials of Osiris is unavailable while Iron Banner is active."
)
#: Static explainer under the VANGUARD ALERTS header (rewards are the weekly-challenge
#: drops).
VANGUARD_EXPLAINER = "Reward listed is for completing the weekly challenge."
#: CONQUESTS (Seasonal Tab) difficulty tiers, in post order. The weekly tier->activity
#: assignment is Portal presentation data the Bungie API does not expose (activities
#: surface as untiered "…: Customize" entries), so this section is hand-curated — see
#: plans/weekly_reset_conquests.md.
CONQUEST_TIERS = ("Expert", "Master", "GM", "Ultimate")

# The seven Pantheon bosses (Pantheon 2.0 roster); the weekly Reprise/Encore pair is
# picked from here by the team — Bungie publishes no forward schedule.
PANTHEON_BOSSES = (
    "Argos",
    "Warpriest",
    "Gahlran",
    "Consecrated Mind",
    "Calus",
    "Morgeth",
    "Insurrection Prime",
)

# Featured raid/dungeon weekly rotators. NO Bungie endpoint exposes these, so they
# are a deterministic cycle over curated ordered lists anchored to a verified reset.
# The anchor + lists below are DEFAULTS; they live in the weekly_reset_config doc and
# are re-derived in tests from the sampled posts (see tests/test_weekly_reset.py).
#
# CONVENTION: the anchor (and the sampled reset timestamps in the tests) are the values
# shown on a post's "Resets:" line — i.e. the *next* Tuesday, when that week's content
# expires — NOT the week's start. `build_draft_context` therefore keys the rotator by
# `next_reset_ts(current_reset_ts())`; keying by `current_reset_ts()` (the week's start)
# instead is off by one week. See `next_reset_ts` and the `build_draft_context` note.
DEFAULT_ROTATOR_ANCHOR = 1782234000  # 2026-06-23 17:00 UTC "Resets:" boundary
DEFAULT_RAID_PAIRS: tuple[tuple[str, str], ...] = (
    ("King's Fall", "Garden of Salvation"),
    ("Root of Nightmares", "Deep Stone Crypt"),
    ("Crota's End", "Vault of Glass"),
    ("Last Wish", "Vow of the Disciple"),
)
DEFAULT_DUNGEON_PAIRS: tuple[tuple[str, str], ...] = (
    ("Spire of the Watcher", "Pit of Heresy"),
    ("Ghosts of the Deep", "Prophecy"),
    ("Warlord's Ruin", "Grasp of Avarice"),
)

# Curated Iron Banner week reset timestamps (unix, Tuesday 17:00 UTC). Trials is off
# on IB weeks. Team maintains this list ~once/episode; empty is fine (they toggle by
# hand).
DEFAULT_IB_WEEK_RESETS: tuple[int, ...] = ()

DEFAULT_CRUCIBLE_1V6 = "Sparrow Racing, Rumble"
# Current season's featured raid/dungeon (the "Weekly Reward" lines); update per season.
DEFAULT_SEASONAL_RAID = "The Desert Perpetual"
DEFAULT_SEASONAL_DUNGEON = "Equilibrium"

# --- Bounded selector domains --------------------------------------------------------
# Small, stable fields are picked from Choice dropdowns instead of free-typed
# autocomplete, to cut the number of inputs. Each list is well under Discord's 25-choice
# limit. (Large domains — GM strikes ~46, weapons — stay on manifest autocomplete.)

# Crucible slots: the first mode of each is fixed; only the second (featured) mode is a
# weekly input. The full mode set (base modes + Labs variants) exceeds Discord's
# 25-choice limit, so the second mode uses autocomplete over CRUCIBLE_MODES rather than
# a Choice selector. Add new Labs modes here as Bungie ships them.
CRUCIBLE_3V3_FIRST = "Competitive"
CRUCIBLE_6V6_FIRST = "Control"
CRUCIBLE_MODES: tuple[str, ...] = (
    # Base modes
    "Clash",
    "Control",
    "Rift",
    "Zone Control",
    "Eruption",
    "Relic",
    "Collision",
    "Momentum Control",
    "Team Scorched",
    "Scorched",
    "Rumble",
    "Survival",
    "Elimination",
    "Countdown",
    "Breakthrough",
    "Lockdown",
    "Salvage",
    "Showdown",
    "Mayhem",
    "Supremacy",
    "Doubles",
    # Labs / rotator variants
    "Heavy Metal",
    "Heavy Metal Supremacy",
    "Hardware",
    "Hardware Mix",
    "Hardware Supremacy",
    "Checkmate Clash",
    "Checkmate Control",
    "Checkmate Countdown",
    "Checkmate Rumble",
    "Checkmate Survival",
    "Checkmate Mix",
    "Classic Mix",
    "Rush Remixed",
)  # > 25 -> autocomplete, not a Choice selector
# Raid / dungeon domains (from the manifest; a new one ships ~1-2x/year — add it here).
RAIDS: tuple[str, ...] = (
    "Crota's End",
    "Crown of Sorrow",
    "Deep Stone Crypt",
    "Garden of Salvation",
    "King's Fall",
    "Last Wish",
    "Leviathan",
    "Leviathan, Eater of Worlds",
    "Leviathan, Spire of Stars",
    "Root of Nightmares",
    "Salvation's Edge",
    "Scourge of the Past",
    "The Desert Perpetual",
    "Vault of Glass",
    "Vow of the Disciple",
)  # 15 < 25
DUNGEONS: tuple[str, ...] = (
    "Duality",
    "Equilibrium",
    "Ghosts of the Deep",
    "Grasp of Avarice",
    "Pit of Heresy",
    "Prophecy",
    "Spire of the Watcher",
    "Sundered Doctrine",
    "The Shattered Throne",
    "Vesper's Host",
    "Warlord's Ruin",
)  # 11 < 25


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
#
# ``WeaponRef`` (the weapon slot shared with the Trials producer) lives in
# ``hybrid_post_core`` and is imported at the top of this module.


@dataclasses.dataclass
class WeeklyResetContext:
    """Every fillable slot in the Weekly Reset Overview post.

    Round-trips through the ``weekly_reset_draft`` RotationData row so an edit session
    survives bot restarts and can be resumed by any owner.
    """

    reset_ts: int
    # VANGUARD ALERTS. The GM strike/weapon are Portal-derived; the Quickplay/Control
    # featured weapons are set by hand — the API exposes only the daily reward or the
    # full weekly pool, not which pool weapon is this week's featured one.
    gm_strike: str = ""
    gm_weapon: WeaponRef | None = None
    quickplay_weapon: WeaponRef | None = None
    control_weapon: WeaponRef | None = None
    seasonal_raid: str = DEFAULT_SEASONAL_RAID
    seasonal_dungeon: str = DEFAULT_SEASONAL_DUNGEON
    # ZAVALA'S WEAPON — set by hand (the vendor API doesn't expose the weekly weapon).
    zavala_weapon: WeaponRef | None = None
    # FEATURED RAIDS & DUNGEONS (weekly rotators)
    rotator_raids: tuple[str, str] = ("", "")
    rotator_dungeons: tuple[str, str] = ("", "")
    # FEATURED PANTHEON
    pantheon_reprise: str = ""
    pantheon_encore: str = ""
    # CRUCIBLE OPS
    crucible_1v6: str = DEFAULT_CRUCIBLE_1V6
    crucible_3v3: str = ""
    crucible_6v6: str = ""
    # CONQUESTS (Seasonal Tab) — hand-curated per tier (not API-derivable). Keys are
    # CONQUEST_TIERS; values are activity-name lists.
    conquests: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    # UPDATES & EVENTS / Trials
    iron_banner: bool = False
    trials_active: bool = True
    #: Optional Bungie patch-notes link, ``{"label": ..., "url": ...}``.
    update_link: dict[str, str] | None = None
    # Editorial
    image_url: str | None = None
    events_narrative: str = ""
    notes: list[str] = dataclasses.field(default_factory=list)
    extra_links: list[dict[str, str]] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, t.Any]:
        return {
            "reset_ts": self.reset_ts,
            "gm_strike": self.gm_strike,
            "gm_weapon": self.gm_weapon.to_dict() if self.gm_weapon else None,
            "quickplay_weapon": (
                self.quickplay_weapon.to_dict() if self.quickplay_weapon else None
            ),
            "control_weapon": (
                self.control_weapon.to_dict() if self.control_weapon else None
            ),
            "seasonal_raid": self.seasonal_raid,
            "seasonal_dungeon": self.seasonal_dungeon,
            "zavala_weapon": self.zavala_weapon.to_dict()
            if self.zavala_weapon
            else None,
            "rotator_raids": list(self.rotator_raids),
            "rotator_dungeons": list(self.rotator_dungeons),
            "pantheon_reprise": self.pantheon_reprise,
            "pantheon_encore": self.pantheon_encore,
            "crucible_1v6": self.crucible_1v6,
            "crucible_3v3": self.crucible_3v3,
            "crucible_6v6": self.crucible_6v6,
            "conquests": {k: list(v) for k, v in self.conquests.items()},
            "iron_banner": self.iron_banner,
            "trials_active": self.trials_active,
            "update_link": dict(self.update_link) if self.update_link else None,
            "image_url": self.image_url,
            "events_narrative": self.events_narrative,
            "notes": list(self.notes),
            "extra_links": [dict(link) for link in self.extra_links],
        }

    @classmethod
    def from_dict(cls, d: t.Mapping[str, t.Any]) -> "WeeklyResetContext":
        def weapon(key: str) -> WeaponRef | None:
            raw = d.get(key)
            return WeaponRef.from_dict(raw) if raw else None

        def pair(key: str) -> tuple[str, str]:
            raw = list(d.get(key) or ["", ""])
            raw = (raw + ["", ""])[:2]
            return (raw[0], raw[1])

        return cls(
            reset_ts=int(d["reset_ts"]),
            gm_strike=d.get("gm_strike", ""),
            gm_weapon=weapon("gm_weapon"),
            quickplay_weapon=weapon("quickplay_weapon"),
            control_weapon=weapon("control_weapon"),
            seasonal_raid=d.get("seasonal_raid", DEFAULT_SEASONAL_RAID),
            seasonal_dungeon=d.get("seasonal_dungeon", DEFAULT_SEASONAL_DUNGEON),
            zavala_weapon=weapon("zavala_weapon"),
            rotator_raids=pair("rotator_raids"),
            rotator_dungeons=pair("rotator_dungeons"),
            pantheon_reprise=d.get("pantheon_reprise", ""),
            pantheon_encore=d.get("pantheon_encore", ""),
            crucible_1v6=d.get("crucible_1v6", DEFAULT_CRUCIBLE_1V6),
            crucible_3v3=d.get("crucible_3v3", ""),
            crucible_6v6=d.get("crucible_6v6", ""),
            conquests={
                str(k): [str(x) for x in (v or [])]
                for k, v in (d.get("conquests") or {}).items()
            },
            iron_banner=bool(d.get("iron_banner", False)),
            trials_active=bool(d.get("trials_active", True)),
            update_link=dict(d["update_link"]) if d.get("update_link") else None,
            image_url=d.get("image_url"),
            events_narrative=d.get("events_narrative", ""),
            notes=list(d.get("notes") or []),
            extra_links=[dict(link) for link in d.get("extra_links") or []],
        )


@dataclasses.dataclass
class WeeklyResetConfig:
    """Carried-over curated data the Bungie API cannot supply.

    Structural constants (rotator order/anchor, Pantheon pool, IB schedule) plus the
    last values the team entered, so next week's draft starts pre-filled, not blank.
    """

    seasonal_raid: str = DEFAULT_SEASONAL_RAID
    seasonal_dungeon: str = DEFAULT_SEASONAL_DUNGEON
    rotator_anchor: int = DEFAULT_ROTATOR_ANCHOR
    raid_pairs: tuple[tuple[str, str], ...] = DEFAULT_RAID_PAIRS
    dungeon_pairs: tuple[tuple[str, str], ...] = DEFAULT_DUNGEON_PAIRS
    pantheon_pool: tuple[str, ...] = PANTHEON_BOSSES
    ib_week_resets: tuple[int, ...] = DEFAULT_IB_WEEK_RESETS
    crucible_1v6: str = DEFAULT_CRUCIBLE_1V6
    crucible_3v3: str = ""
    crucible_6v6: str = ""
    default_image_url: str | None = None
    event_image_map: dict[str, str] = dataclasses.field(default_factory=dict)
    # Last-entered editorial values, for pre-fill continuity.
    last_pantheon_reprise: str = ""
    last_pantheon_encore: str = ""

    def to_dict(self) -> dict[str, t.Any]:
        return {
            "seasonal_raid": self.seasonal_raid,
            "seasonal_dungeon": self.seasonal_dungeon,
            "rotator_anchor": self.rotator_anchor,
            "raid_pairs": [list(p) for p in self.raid_pairs],
            "dungeon_pairs": [list(p) for p in self.dungeon_pairs],
            "pantheon_pool": list(self.pantheon_pool),
            "ib_week_resets": list(self.ib_week_resets),
            "crucible_1v6": self.crucible_1v6,
            "crucible_3v3": self.crucible_3v3,
            "crucible_6v6": self.crucible_6v6,
            "default_image_url": self.default_image_url,
            "event_image_map": dict(self.event_image_map),
            "last_pantheon_reprise": self.last_pantheon_reprise,
            "last_pantheon_encore": self.last_pantheon_encore,
        }

    @classmethod
    def from_dict(cls, d: t.Mapping[str, t.Any] | None) -> "WeeklyResetConfig":
        if not d:
            return cls()

        def pairs(key: str, fallback: tuple[tuple[str, str], ...]):
            raw = d.get(key)
            if not raw:
                return fallback
            return tuple((str(p[0]), str(p[1])) for p in raw)

        return cls(
            seasonal_raid=d.get("seasonal_raid", DEFAULT_SEASONAL_RAID),
            seasonal_dungeon=d.get("seasonal_dungeon", DEFAULT_SEASONAL_DUNGEON),
            rotator_anchor=int(d.get("rotator_anchor", DEFAULT_ROTATOR_ANCHOR)),
            raid_pairs=pairs("raid_pairs", DEFAULT_RAID_PAIRS),
            dungeon_pairs=pairs("dungeon_pairs", DEFAULT_DUNGEON_PAIRS),
            pantheon_pool=tuple(d.get("pantheon_pool") or PANTHEON_BOSSES),
            ib_week_resets=tuple(int(x) for x in d.get("ib_week_resets") or ()),
            crucible_1v6=d.get("crucible_1v6", DEFAULT_CRUCIBLE_1V6),
            crucible_3v3=d.get("crucible_3v3", ""),
            crucible_6v6=d.get("crucible_6v6", ""),
            default_image_url=d.get("default_image_url"),
            event_image_map=dict(d.get("event_image_map") or {}),
            last_pantheon_reprise=d.get("last_pantheon_reprise", ""),
            last_pantheon_encore=d.get("last_pantheon_encore", ""),
        )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def load_config() -> WeeklyResetConfig:
    return WeeklyResetConfig.from_dict(await schemas.RotationData.get_data(CONFIG_SLUG))


async def save_config(config: WeeklyResetConfig) -> None:
    await schemas.RotationData.set_data(CONFIG_SLUG, config.to_dict())


async def load_draft() -> WeeklyResetContext | None:
    data = await schemas.RotationData.get_data(DRAFT_SLUG)
    return WeeklyResetContext.from_dict(data) if data else None


async def save_draft(ctx: WeeklyResetContext) -> None:
    await schemas.RotationData.set_data(DRAFT_SLUG, ctx.to_dict())


# ---------------------------------------------------------------------------
# Reset-time + rotator computation (deterministic, no API)
# ---------------------------------------------------------------------------
#
# ``current_reset_ts`` / ``next_reset_ts`` / ``rotator_index`` / ``compute_rotator``
# (plus ``REFERENCE_RESET`` / ``WEEK``) are generic and live in ``hybrid_post_core``;
# ``current_reset_ts``, ``next_reset_ts`` and ``compute_rotator`` are imported above.


# ---------------------------------------------------------------------------
# Bungie derivations (all best-effort — any failure leaves the slot for the team)
# ---------------------------------------------------------------------------


class PortalDerivation(t.NamedTuple):
    gm_strike: str
    gm_weapon: WeaponRef | None


# Portal (component-204) derivation signature. Only the GM Nightfall is derived from the
# Portal: it's the one weekly-stable weapon the API exposes (the featured Nightfall is
# the same all week, so its guaranteed reward is too). The Quickplay/Control featured
# weapons are set by hand — the API only surfaces the daily reward or the full weekly
# pool, never the single featured weekly weapon (set via `/weekly_reset set_reward`).
_STRIKE_ACTIVITY_TYPE_HASH = 556925641  # DestinyActivityTypeDefinition "Strike"


async def derive_portal_fields() -> PortalDerivation:
    """GM Nightfall strike + reward weapon from the authed Portal (component 204).

    The GM Nightfall is the only Strike-type featured op carrying the weekly Nightfall
    *challenge* (ordinary playlist strikes have none), and its guaranteed reward is the
    weekly GM weapon. Both stay correctable via `/weekly_reset set_reward`; anything
    Bungie doesn't surface is left blank/None.
    """
    try:
        ops = await portal_ops.fetch_portal_ops()
    except Exception:
        logger.warning("weekly_reset: fetch_portal_ops failed", exc_info=True)
        return PortalDerivation("", None)

    for op in ops:
        is_gm = op.activity_type_hash == _STRIKE_ACTIVITY_TYPE_HASH
        if is_gm and op.challenge_count > 0:
            gm_weapon = (
                WeaponRef(name=op.reward_name, hash=op.reward_hash)
                if op.reward_hash
                else None
            )
            return PortalDerivation(op.activity_name, gm_weapon)
    return PortalDerivation("", None)


async def build_draft_context(
    config: WeeklyResetConfig | None = None,
) -> WeeklyResetContext:
    """Assemble a fresh draft: compute + best-effort API + carried-over config."""
    config = config or await load_config()
    reset_ts = current_reset_ts()
    # Weekly rotations (raids/dungeons, IB schedule) MUST be keyed by the boundary shown
    # on the "Resets:" line — the *next* Tuesday, when this week's content expires — as
    # that is the convention the anchor/sample data are calibrated to (see
    # DEFAULT_ROTATOR_ANCHOR). Keying by ``reset_ts`` (the week's *start*) instead
    # retrieves the *previous* week's rotation (off-by-one).
    rotation_ts = next_reset_ts(reset_ts)

    ctx = WeeklyResetContext(reset_ts=reset_ts)
    # Carried-over / deterministic fields.
    ctx.seasonal_raid = config.seasonal_raid
    ctx.seasonal_dungeon = config.seasonal_dungeon
    ctx.rotator_raids = compute_rotator(
        config.raid_pairs, config.rotator_anchor, rotation_ts
    )
    ctx.rotator_dungeons = compute_rotator(
        config.dungeon_pairs, config.rotator_anchor, rotation_ts
    )
    ctx.pantheon_reprise = config.last_pantheon_reprise
    ctx.pantheon_encore = config.last_pantheon_encore
    ctx.crucible_1v6 = config.crucible_1v6
    ctx.crucible_3v3 = config.crucible_3v3
    ctx.crucible_6v6 = config.crucible_6v6
    ctx.iron_banner = rotation_ts in config.ib_week_resets
    ctx.trials_active = not ctx.iron_banner
    ctx.image_url = config.default_image_url

    # Best-effort Portal (component-204) derivation (never fatal — team fills gaps).
    # Only the weekly GM strike + weapon come from the Portal. The Quickplay/Control and
    # Zavala weapons are manual (`/weekly_reset set_reward`): the API exposes only the
    # daily reward or the full weekly pool, not the single featured weekly weapon.
    derived = await derive_portal_fields()
    ctx.gm_strike = derived.gm_strike
    ctx.gm_weapon = derived.gm_weapon

    return ctx


# ---------------------------------------------------------------------------
# Components V2 renderer
# ---------------------------------------------------------------------------


def _weekly_reward(name: str) -> str:
    return f"{name} - Weekly Reward" if name else "Weekly Reward"


def build_body(ctx: WeeklyResetContext) -> str:
    """The full post markdown, with ``:emoji:`` tokens still un-substituted."""
    lines: list[str] = [
        "# Weekly Reset Overview",
        "",
        f"Resets: <t:{next_reset_ts(ctx.reset_ts)}:f>",
    ]

    # UPDATES & EVENTS — the Bungie patch link, the Trials-returns reminder, and any
    # editorial events. Trials is mutually exclusive with Iron Banner weeks.
    trials_line = ctx.trials_active and not ctx.iron_banner
    if ctx.update_link or ctx.iron_banner or ctx.events_narrative or trials_line:
        lines += ["", "**UPDATES & EVENTS**", ""]
        if ctx.update_link:
            label = ctx.update_link.get("label") or "Update"
            url = ctx.update_link.get("url") or ""
            if url:
                lines.append(f":Bungie: {SEP} [{label}]({url})")
        if trials_line:
            lines.append(f":trials: {SEP} Trials returns on Friday at reset")
        if ctx.iron_banner:
            lines.append(f":IronBanner: {SEP} Iron Banner has returned!")
            lines += ["", TRIALS_IB_REMINDER]
        if ctx.events_narrative:
            lines += ["", ctx.events_narrative]

    # VANGUARD ALERTS. GM is Portal-derived; Quickplay/Control are the manually-set
    # weekly featured weapons (the API exposes only the daily reward or the full pool).
    lines += ["", "**VANGUARD ALERTS**", "", VANGUARD_EXPLAINER, ""]
    if ctx.quickplay_weapon:
        lines.append(
            f":vanguard_strikes: {SEP} Quickplay - {ctx.quickplay_weapon.markdown()}"
        )
    gm_weapon = f" - {ctx.gm_weapon.markdown()}" if ctx.gm_weapon else ""
    if ctx.gm_strike or ctx.gm_weapon:
        lines.append(f":gm_nightfall: {SEP} GM Alert: {ctx.gm_strike}{gm_weapon}")
    if ctx.control_weapon:
        lines.append(f":crucible: {SEP} Control - {ctx.control_weapon.markdown()}")
    if ctx.seasonal_raid:
        lines.append(f":raid: {SEP} {_weekly_reward(ctx.seasonal_raid)}")
    if ctx.seasonal_dungeon:
        lines.append(f":dungeon: {SEP} {_weekly_reward(ctx.seasonal_dungeon)}")

    # CONQUESTS (Seasonal Tab) — one line per non-empty tier, in CONQUEST_TIERS order.
    # Hand-curated; the API can't supply the weekly tier->activity map (see the plan).
    if any(ctx.conquests.get(tier) for tier in CONQUEST_TIERS):
        lines += ["", "**CONQUESTS (Seasonal Tab)**", ""]
        for tier in CONQUEST_TIERS:
            activities = [a for a in ctx.conquests.get(tier, []) if a]
            if activities:
                lines.append(f":Conquests: {SEP} {tier}: {', '.join(activities)}")

    # FEATURED RAIDS & DUNGEONS
    if any(ctx.rotator_raids) or any(ctx.rotator_dungeons):
        lines += ["", "**FEATURED RAIDS & DUNGEONS**", ""]
        if any(ctx.rotator_raids):
            lines.append(
                f":raid: {SEP} {' + '.join(x for x in ctx.rotator_raids if x)}"
            )
        if any(ctx.rotator_dungeons):
            lines.append(
                f":dungeon: {SEP} {' + '.join(x for x in ctx.rotator_dungeons if x)}"
            )

    # Ad-hoc info notes (e.g. "Duality is available due to a bug").
    for note in ctx.notes:
        if note:
            lines += ["", f":info: {note}"]

    # FEATURED PANTHEON
    if ctx.pantheon_reprise or ctx.pantheon_encore:
        lines += ["", "**FEATURED PANTHEON**", ""]
        if ctx.pantheon_reprise:
            lines.append(f":Pantheon: {SEP} Reprise: {ctx.pantheon_reprise}")
        if ctx.pantheon_encore:
            lines.append(f":Pantheon: {SEP} Encore: {ctx.pantheon_encore}")

    # ZAVALA'S WEAPON
    if ctx.zavala_weapon:
        emoji = ctx.zavala_weapon.emoji_name or "weapon"
        lines += [
            "",
            "**ZAVALA'S WEAPON**",
            "",
            f":{emoji}: {SEP} {ctx.zavala_weapon.markdown()} {ZAVALA_TIER_SUFFIX}",
        ]

    # CRUCIBLE OPS
    if ctx.crucible_1v6 or ctx.crucible_3v3 or ctx.crucible_6v6:
        lines += ["", "**CRUCIBLE OPS**", ""]
        if ctx.crucible_1v6:
            lines.append(f":crucible: {SEP} 1v6: {ctx.crucible_1v6}")
        if ctx.crucible_3v3:
            lines.append(f":crucible: {SEP} 3v3: {ctx.crucible_3v3}")
        if ctx.crucible_6v6:
            lines.append(f":crucible: {SEP} 6v6: {ctx.crucible_6v6}")

    # MORE
    lines += [
        "",
        "**MORE**",
        "",
        f"[**View Legacy Activities**]({LEGACY_ACTIVITIES_URL}) ↗",
    ]
    for link in ctx.extra_links:
        label, url = link.get("label"), link.get("url")
        if label and url:
            lines.append(f"[**{label}**]({url}) ↗")

    lines += ["", SIGN_OFF]
    return "\n".join(lines)


async def format_weekly_reset(ctx: WeeklyResetContext, bot: CachedFetchBot) -> HMessage:
    """Render the context to a Components V2 :class:`HMessage`."""
    hmsg = build_cv2(build_body(ctx), ctx.image_url)
    # Resolve :emoji: on the assembled message, then cap CV2 text (naive front-to-back
    # truncate + CRITICAL alert on overflow).
    substitute_guild_emoji(hmsg, await fetch_emoji_dict(bot))
    return await guard_cv2_hmessage(hmsg, post_name="Weekly Reset")


async def weekly_reset_message_constructor(bot: CachedFetchBot) -> HMessage:
    """Announcer hook: render the current draft (build a fresh one if none is saved)."""
    ctx = await load_draft()
    if ctx is None:
        ctx = await build_draft_context()
    return await format_weekly_reset(ctx, bot)


def validate_post(ctx: WeeklyResetContext) -> list[str]:
    """Problems that would make the post empty or break Components V2 limits."""
    problems: list[str] = []
    body = build_body(ctx)
    if len(body) > 3900:
        problems.append(
            f"Post is too long ({len(body)}/3900 chars) — trim some sections."
        )
    if not (ctx.quickplay_weapon or ctx.gm_strike or ctx.zavala_weapon):
        problems.append(
            "Post looks empty — fill in at least the Vanguard/Zavala section."
        )
    if ctx.image_url and not ctx.image_url.startswith(("http://", "https://")):
        problems.append("Image URL must start with http:// or https://.")
    if "weekly_reset" not in cfg.followables:
        problems.append("No 'weekly_reset' entry in FOLLOWABLES — nowhere to publish.")
    return problems


# ---------------------------------------------------------------------------
# Rich HTML preview (web form)
# ---------------------------------------------------------------------------
#
# The safe markdown->HTML preview renderer (``render_post_html`` + its ``_INLINE_MD`` /
# ``_render_line`` / emoji-substituter internals) is generic and lives in
# ``hybrid_post_core``; ``render_post_html`` + ``_format_reset_ts`` are imported above.


# ---------------------------------------------------------------------------
# Draft metadata (post message id, publish status, "needs attention" flags)
# ---------------------------------------------------------------------------
#
# ``DraftMeta`` (the post lifecycle record, incl. ``reset_ts`` + ``is_current``) is
# generic and lives in ``hybrid_post_core``; it is imported above. Only the
# followable-specific slug and its load/save helpers stay here.

META_SLUG = "weekly_reset_meta"


async def load_meta() -> DraftMeta:
    return DraftMeta.from_dict(await schemas.RotationData.get_data(META_SLUG))


async def save_meta(meta: DraftMeta) -> None:
    await schemas.RotationData.set_data(META_SLUG, meta.to_dict())


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------


async def _render_for_spec(ctx: WeeklyResetContext, bot: CachedFetchBot) -> HMessage:
    """``HybridPostSpec.render`` hook, indirecting through the module global so a test
    that monkeypatches ``format_weekly_reset`` is honoured by the shared publish
    core."""
    return await format_weekly_reset(ctx, bot)


def _now_reset_ts() -> int:
    """``HybridPostSpec.current_reset_ts`` hook: the current reset-period boundary.

    Indirects through the module global so a test that monkeypatches
    ``weekly_reset.current_reset_ts`` steers the shared route code's notion of "now".
    """
    return current_reset_ts()


# ``_SPEC`` (the HybridPostSpec wiring this producer to the shared core) is constructed
# at the bottom of the module, once every hook it references is defined; the wrappers
# and route handlers below resolve it at call time.


async def post_or_edit_unpublished(
    bot: CachedFetchBot, ctx: WeeklyResetContext, meta: DraftMeta
) -> DraftMeta:
    """Create-or-update the *uncrossposted* in-channel post (delegates to the core)."""
    return await hybrid_post_core.post_or_edit_unpublished(_SPEC, bot, ctx, meta)


async def publish_draft(
    bot: CachedFetchBot, ctx: WeeklyResetContext, meta: DraftMeta
) -> tuple[DraftMeta, str]:
    """Publish (crosspost) the in-channel post (delegates to the core)."""
    return await hybrid_post_core.publish_draft(_SPEC, bot, ctx, meta)


# ---------------------------------------------------------------------------
# Single-writer lock
# ---------------------------------------------------------------------------

#: Serialises read-modify-write of the shared draft doc (single bot process).
_draft_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Reset-day cron
# ---------------------------------------------------------------------------


async def run_reset_draft(bot: CachedFetchBot, *, ping_owners: bool) -> None:
    """Build a fresh draft and post it as the new week's *uncrossposted* channel post.

    A fresh ``DraftMeta`` (``message_id == 0``) means the post is created anew each
    Tuesday, clearing last week's message id; publishing (the crosspost) stays manual.
    """
    config = await load_config()
    ctx = await build_draft_context(config)

    async with _draft_lock:
        meta = DraftMeta(
            status="draft",
            last_edited_ts=int(dt.datetime.now(tz=dt.UTC).timestamp()),
        )
        await save_draft(ctx)
        meta = await post_or_edit_unpublished(bot, ctx, meta)
        await save_meta(meta)

    if ping_owners:
        logger.info("weekly_reset: fresh draft posted (uncrossposted) for the new week")
        # TODO(step5): notify owners with the web draft link


# ---------------------------------------------------------------------------
# Manifest-backed option pools + apply mutators
# ---------------------------------------------------------------------------

# Reward slots as (label, attribute): the web form renders one weapon picker per entry
# and ``apply_reward_field`` writes the resolved WeaponRef back.
_REWARD_FIELDS: tuple[tuple[str, str], ...] = (
    ("GM Nightfall reward weapon", "gm_weapon"),
    ("Vanguard / Quickplay weapon", "quickplay_weapon"),
    ("Crucible / Control weapon", "control_weapon"),
    ("Zavala's Weapon", "zavala_weapon"),
)

# DestinyActivityModeType ids used to classify activities (raid/dungeon vs strike).
_MODE_RAID = 4
_MODE_DUNGEON = 82
# Variant/difficulty suffixes (after a ": ") stripped to reach the base activity name.
_VARIANT_SUFFIXES = frozenset(
    {
        "standard",
        "prestige",
        "normal",
        "master",
        "legend",
        "adept",
        "expert",
        "advanced",
        "hero",
        "grandmaster",
        "beginner",
        "challenge mode",
        "eternity",
        "explorer",
        "explorer (matchmade)",
        "ultimatum",
        "customize",
        "matchmade",
        "private",
        "epic",
        "contest",
    }
)
# Whole names that are only a difficulty tier — never a real activity.
_DIFFICULTY_ONLY = frozenset(
    {
        "adept",
        "advanced",
        "expert",
        "grandmaster",
        "hero",
        "legend",
        "master",
        "normal",
        "beginner",
    }
)
# Prefixes stripped from GM strike names (difficulty/quest wrappers).
_STRIKE_PREFIXES = (
    "Nightfall Grandmaster: ",
    "Grandmaster Nightfall: ",
    "Grandmaster: ",
    "Nightfall: ",
    "Legendary ",
    "Legend ",
    "QUEST: ",
    "Quest: ",
)
# Substrings marking non-strike playlist/event junk dropped from the GM strike pool.
# "battlegrounds"/"strikes" (plural) are playlists; singular "Battleground: X" stays.
_STRIKE_JUNK = (
    "guardian games",
    "contest of elders",
    "fireteam ops",
    "crucible",
    "armsweek",
    "playlist",
    "training",
    "rushdown",
    "blitz",
    "battlegrounds",
    "strikes",
)


# Conquest activities are named "<Tier> Conquest: <Base>: Customize" in the manifest.
# <Base> may contain its own colon (e.g. "Operation: Seraph's Shield"), so capture it
# greedily between the fixed prefix and the ": Customize" suffix (don't split on ":").
_CONQUEST_NAME_RE = re.compile(r"^(\S+) Conquest: (.+): Customize$")
#: Manifest tier word -> the post's CONQUEST_TIERS label ("Grandmaster" -> "GM").
_CONQUEST_MANIFEST_TIER = {
    "Expert": "Expert",
    "Master": "Master",
    "Grandmaster": "GM",
    "Ultimate": "Ultimate",
}


def _parse_conquest_name(raw_name: str) -> tuple[str, str] | None:
    """Parse a manifest Conquest activity name into ``(post_tier, base_name)``.

    ``"Expert Conquest: Sunless Cell: Customize"`` -> ``("Expert", "Sunless Cell")``;
    ``"Grandmaster Conquest: Scarlet Keep: Customize"`` -> ``("GM", "Scarlet Keep")``.
    Returns ``None`` for any non-Conquest name (plain strikes, ``: Customize`` missions,
    etc.), which is how the pool excludes the non-Conquest variants.
    """
    match = _CONQUEST_NAME_RE.match(raw_name.strip())
    if not match:
        return None
    tier = _CONQUEST_MANIFEST_TIER.get(match.group(1))
    return (tier, match.group(2).strip()) if tier else None


@dataclasses.dataclass
class _Indexes:
    """Manifest-derived autocomplete data, built once and cached."""

    #: (name, hash, itemTypeDisplayName, itemType, rarity) per weapon/armour, deduped.
    items: list[tuple[str, int, str, int, str]]
    #: category ("raid"/"dungeon"/"strike"/"pantheon"/"crucible") -> sorted names.
    activities: dict[str, list[str]]
    #: Conquests pool: post tier ("Expert"/"Master"/"GM"/"Ultimate") -> sorted names.
    conquests: dict[str, list[str]]


_indexes: _Indexes | None = None
_indexes_lock = asyncio.Lock()


def _classify_activity(defn: dict[str, t.Any], type_name: str = "") -> str | None:
    """Classify a DestinyActivityDefinition, or None if it's none of our categories.

    ``type_name`` is the activity's resolved DestinyActivityTypeDefinition name — the
    authoritative signal. Pantheon is checked first (its encounters carry raid mode and
    would otherwise leak into raids); the GM strike pool is Strikes + Battlegrounds; the
    fireteam-size fallback only fires when there is neither a type nor a mode.
    """
    name = ((defn.get("displayProperties") or {}).get("name") or "").strip()
    low = name.lower()
    # Pantheon reprise/encore encounters — the featured boss names live in these.
    if low.startswith("featured reprise: ") or low.startswith("featured encore: "):
        return "pantheon"
    if "pantheon" in low:
        return None  # wings / customize variants — not a reprise/encore boss

    type_lower = type_name.lower()
    if type_lower == "raid":
        return "raid"
    if type_lower == "dungeon":
        return "dungeon"
    if type_lower == "strike" or "battleground" in low:
        return "strike"

    modes = set(defn.get("activityModeTypes") or [])
    direct = defn.get("directActivityModeType")
    if direct:
        modes.add(direct)
    if _MODE_RAID in modes:
        return "raid"
    if _MODE_DUNGEON in modes:
        return "dungeon"

    if not type_name and not modes:
        max_party = (defn.get("matchmaking") or {}).get("maxParty")
        if max_party == 6:
            return "raid"
        if max_party == 3:
            return "dungeon"
    return None


def _strip_variant(name: str) -> str:
    """Reduce a name to its base by dropping variant suffixes + a trailing '(...)'."""
    while ": " in name:
        base, _, suffix = name.rpartition(": ")
        suffix = suffix.strip().lower()
        if suffix in _VARIANT_SUFFIXES or re.fullmatch(r"level \d+", suffix):
            name = base.strip()
        else:
            break
    return re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()


def _clean_activity_name(name: str, category: str) -> str:
    """Normalise to the base name; "" to drop a variant/tier/junk entry."""
    name = name.strip()
    if not name:
        return ""
    if category == "pantheon":
        for prefix in ("Featured Reprise: ", "Featured Encore: "):
            if name.startswith(prefix):
                return name[len(prefix) :].split(":")[0].strip()
        return ""
    if category == "strike":
        for prefix in _STRIKE_PREFIXES:
            if name.startswith(prefix):
                name = name[len(prefix) :]
                break
    base = _strip_variant(name)
    if not base or base.lower() in _DIFFICULTY_ONLY:
        return ""
    if category == "strike" and any(junk in base.lower() for junk in _STRIKE_JUNK):
        return ""
    return base


async def _build_indexes() -> _Indexes:
    # One row per named weapon/armour (deduped, newest hash wins) — the weapon pool is
    # generic, so building it lives in hybrid_post_core.iter_weapon_items. Seed with an
    # empty list so a manifest failure still yields a usable (strike/conquest) index.
    items: list[tuple[str, int, str, int, str]] = []
    # Only GM strikes need manifest autocomplete now; raids/dungeons/pantheon/crucible
    # are bounded Choice selectors (see the *_CHOICES constants).
    strikes: set[str] = set()
    # Conquests pool, bucketed by post tier: only the manifest "<Tier> Conquest: <Base>:
    # Customize" activities, keyed by tier so the autocomplete matches the picked tier.
    conquest_by_tier: dict[str, set[str]] = {tier: set() for tier in CONQUEST_TIERS}
    try:
        path = await api._get_latest_manifest(schemas.BungieCredentials.api_key)
        async with aiosqlite.connect(path) as con:
            cur = await con.cursor()

            items = await iter_weapon_items(cur)

            # Activity type names are the authoritative raid/dungeon/nightfall signal.
            await cur.execute("SELECT json FROM DestinyActivityTypeDefinition")
            activity_types: dict[int, str] = {}
            for (row,) in await cur.fetchall():
                defn = json.loads(row)
                activity_types[int(defn["hash"])] = (
                    defn.get("displayProperties") or {}
                ).get("name", "")

            await cur.execute("SELECT json FROM DestinyActivityDefinition")
            for (row,) in await cur.fetchall():
                defn = json.loads(row)
                raw_name = (defn.get("displayProperties") or {}).get("name", "")
                # Conquests: keep only the "<Tier> Conquest: <Base>: Customize" entries,
                # bucketed by tier — independent of the strike cleaning below.
                parsed = _parse_conquest_name(raw_name)
                if parsed:
                    conquest_by_tier[parsed[0]].add(parsed[1])
                type_name = activity_types.get(defn.get("activityTypeHash"), "")
                if _classify_activity(defn, type_name) == "strike":
                    cleaned = _clean_activity_name(raw_name, "strike")
                    if cleaned:
                        strikes.add(cleaned)
    except Exception:
        logger.warning("weekly_reset: manifest index build failed", exc_info=True)

    result = _Indexes(
        items=items,
        activities={"strike": sorted(strikes)},
        conquests={tier: sorted(names) for tier, names in conquest_by_tier.items()},
    )
    logger.info(
        "weekly_reset indexes: %d items; strikes=%d; conquests=%d",
        len(result.items),
        len(result.activities["strike"]),
        sum(len(names) for names in result.conquests.values()),
    )
    return result


async def get_indexes() -> _Indexes:
    """Build (once) and cache the manifest-backed autocomplete indexes."""
    global _indexes
    if _indexes is not None:
        return _indexes
    async with _indexes_lock:
        if _indexes is None:
            _indexes = await _build_indexes()
        return _indexes


async def resolve_reward_value(value: str) -> WeaponRef | None:
    """A hash (picked from autocomplete) -> full WeaponRef; else a plain typed name."""
    return resolve_weapon(value, (await get_indexes()).items)


def apply_gm_strike(ctx: WeeklyResetContext, value: str) -> None:
    ctx.gm_strike = value


def apply_crucible(ctx: WeeklyResetContext, three: str, six: str) -> None:
    """First mode of each slot is fixed; only the featured (second) mode is chosen."""
    if three:
        ctx.crucible_3v3 = f"{CRUCIBLE_3V3_FIRST}, {three}"
    if six:
        ctx.crucible_6v6 = f"{CRUCIBLE_6V6_FIRST}, {six}"


def apply_conquests(ctx: WeeklyResetContext, tier: str, value: str) -> None:
    """Replace one Conquests tier's activity list from a comma-separated string.

    An empty ``value`` clears that tier.
    """
    activities = [part.strip() for part in value.split(",") if part.strip()]
    if activities:
        ctx.conquests[tier] = activities
    else:
        ctx.conquests.pop(tier, None)


def apply_update(ctx: WeeklyResetContext, label: str, url: str) -> None:
    """Set (or clear, when ``url`` is blank) the UPDATES & EVENTS Bungie patch link."""
    label, url = label.strip(), url.strip()
    ctx.update_link = {"label": label or "Update", "url": url} if url else None


def apply_pantheon(ctx: WeeklyResetContext, reprise: str, encore: str) -> None:
    if reprise:
        ctx.pantheon_reprise = reprise
    if encore:
        ctx.pantheon_encore = encore


def apply_raids(ctx: WeeklyResetContext, seasonal: str, feat1: str, feat2: str) -> None:
    if seasonal:
        ctx.seasonal_raid = seasonal
    if feat1:
        ctx.rotator_raids = (feat1, ctx.rotator_raids[1])
    if feat2:
        ctx.rotator_raids = (ctx.rotator_raids[0], feat2)


def apply_dungeons(
    ctx: WeeklyResetContext, seasonal: str, feat1: str, feat2: str
) -> None:
    if seasonal:
        ctx.seasonal_dungeon = seasonal
    if feat1:
        ctx.rotator_dungeons = (feat1, ctx.rotator_dungeons[1])
    if feat2:
        ctx.rotator_dungeons = (ctx.rotator_dungeons[0], feat2)


def apply_reward_field(
    ctx: WeeklyResetContext, field: str, weapon: WeaponRef | None
) -> None:
    if field in {"gm_weapon", "quickplay_weapon", "control_weapon", "zavala_weapon"}:
        setattr(ctx, field, weapon)


def _parse_links(raw: str) -> list[dict[str, str]]:
    """Parse the Notes & Links textarea ('Label | https://url' per line).

    Kept as the web /save helper (Step 5); only http(s) URLs are accepted.
    """
    links: list[dict[str, str]] = []
    for line in raw.splitlines():
        if "|" not in line:
            continue
        label, url = (part.strip() for part in line.split("|", 1))
        if label and url.startswith(("http://", "https://")):
            links.append({"label": label, "url": url})
    return links


async def mutate_draft(
    invoker_id: int,
    fn: t.Callable[[WeeklyResetContext], None],
) -> None:
    """Load-modify-save the persisted draft under the lock; the web /save primitive.

    Nothing calls this after the Discord input UI was removed; the web form's /save
    route (Step 5) reuses it.
    """
    # Auto-fill from the API the first time a field is set, so reset time, seasonal
    # raid/dungeon and the computed rotators are pre-populated instead of blank. Built
    # outside the lock (it does network I/O), then committed only if still absent.
    if await load_draft() is None:
        seeded = await build_draft_context()
        async with _draft_lock:
            if await load_draft() is None:
                await save_draft(seeded)
    async with _draft_lock:
        draft = await load_draft() or WeeklyResetContext(reset_ts=current_reset_ts())
        fn(draft)
        meta = await load_meta()
        meta.status = "draft"
        meta.last_edited_by = invoker_id
        meta.last_edited_ts = int(dt.datetime.now(tz=dt.UTC).timestamp())
        await save_draft(draft)
        await save_meta(meta)


# ---------------------------------------------------------------------------
# Owner-authenticated web form — routes
# ---------------------------------------------------------------------------
#
# The Discord input UI is gone; input now flows through this form. Auth is enforced
# centrally by the Discord-OAuth middleware in ``web_auth.py`` (which also covers the
# cross-origin defence), so this module carries no auth code. All security-relevant
# transforms (weapon resolution, the Iron-Banner⇒Trials-off rule, link validation) still
# run server-side in :func:`_context_from_payload`; the client payload is never trusted.

_FORM_HTML_PATH = (
    Path(__file__).resolve().parent.parent / "web_static" / "weekly_reset_form.html"
)

#: The live bot, stashed by the StartedEvent listener so the create/edit routes can
#: reach the REST client. A module global (not aiohttp app state) because the listener
#: that holds the bot is DI-injected and never sees the app object, while the routes
#: live in this module — a global is the least-plumbing option. ``None`` until the
#: StartedEvent fires, at which point the create/edit routes stop 503-ing.
_bot: CachedFetchBot | None = None


def _pair(raw: t.Any) -> tuple[str, str]:
    """Coerce an arbitrary client value into a 2-tuple of trimmed strings."""
    values = [str(x).strip() for x in (raw or [])]
    values = (values + ["", ""])[:2]
    return (values[0], values[1])


async def _build_options() -> dict[str, t.Any]:
    """Option pools shipped in the page bootstrap and filtered client-side."""
    indexes = await get_indexes()
    return {
        "items": [
            {"name": name, "hash": hash_, "type": type_name, "rarity": rarity}
            for (name, hash_, type_name, _item_type, rarity) in indexes.items
        ],
        "conquests": {tier: list(names) for tier, names in indexes.conquests.items()},
        "strikes": list(indexes.activities.get("strike", [])),
        "raids": list(RAIDS),
        "dungeons": list(DUNGEONS),
        "pantheon": list(PANTHEON_BOSSES),
        "crucible_modes": list(CRUCIBLE_MODES),
        "crucible_3v3_first": CRUCIBLE_3V3_FIRST,
        "crucible_6v6_first": CRUCIBLE_6V6_FIRST,
    }


async def _context_from_payload(payload: t.Mapping[str, t.Any]) -> WeeklyResetContext:
    """Build a :class:`WeeklyResetContext` from the form JSON, entirely server-side.

    The client is never trusted for security-relevant transforms: each weapon slot is
    resolved from its submitted value (a manifest hash or a typed name) via
    :func:`resolve_reward_value`; the Iron-Banner⇒Trials-off rule is enforced; the
    featured Crucible modes are prefixed with their fixed first mode; notes are split
    per-line and links validated to http(s) via :func:`_parse_links`.
    """
    ctx = WeeklyResetContext(
        reset_ts=int(payload.get("reset_ts") or current_reset_ts())
    )

    ctx.gm_strike = str(payload.get("gm_strike", "")).strip()
    ctx.gm_weapon = await resolve_reward_value(str(payload.get("gm_weapon", "")))
    ctx.quickplay_weapon = await resolve_reward_value(
        str(payload.get("quickplay_weapon", ""))
    )
    ctx.control_weapon = await resolve_reward_value(
        str(payload.get("control_weapon", ""))
    )
    ctx.zavala_weapon = await resolve_reward_value(
        str(payload.get("zavala_weapon", ""))
    )

    ctx.seasonal_raid = str(payload.get("seasonal_raid", DEFAULT_SEASONAL_RAID)).strip()
    ctx.seasonal_dungeon = str(
        payload.get("seasonal_dungeon", DEFAULT_SEASONAL_DUNGEON)
    ).strip()
    ctx.rotator_raids = _pair(payload.get("rotator_raids"))
    ctx.rotator_dungeons = _pair(payload.get("rotator_dungeons"))
    ctx.pantheon_reprise = str(payload.get("pantheon_reprise", "")).strip()
    ctx.pantheon_encore = str(payload.get("pantheon_encore", "")).strip()

    # Crucible: the first mode of each slot is fixed; only the featured (second) mode is
    # a weekly input, prefixed with its fixed first mode (mirrors apply_crucible).
    ctx.crucible_1v6 = str(payload.get("crucible_1v6", "")).strip()
    three = str(payload.get("crucible_3v3", "")).strip()
    six = str(payload.get("crucible_6v6", "")).strip()
    ctx.crucible_3v3 = f"{CRUCIBLE_3V3_FIRST}, {three}" if three else ""
    ctx.crucible_6v6 = f"{CRUCIBLE_6V6_FIRST}, {six}" if six else ""

    raw_conquests = payload.get("conquests") or {}
    ctx.conquests = {
        tier: activities
        for tier in CONQUEST_TIERS
        if (
            activities := [
                str(a).strip() for a in raw_conquests.get(tier, []) if str(a).strip()
            ]
        )
    }

    # Iron Banner and Trials are mutually exclusive — IB forces Trials off, server-side.
    ctx.iron_banner = bool(payload.get("iron_banner", False))
    ctx.trials_active = (
        False if ctx.iron_banner else bool(payload.get("trials_active", True))
    )

    update_url = str(payload.get("update_url", "")).strip()
    update_label = str(payload.get("update_label", "")).strip()
    ctx.update_link = (
        {"label": update_label or "Update", "url": update_url} if update_url else None
    )

    ctx.image_url = str(payload.get("image_url", "")).strip() or None
    ctx.events_narrative = str(payload.get("events_narrative", "")).strip()
    ctx.notes = [
        line.strip()
        for line in str(payload.get("notes_text", "")).splitlines()
        if line.strip()
    ]
    ctx.extra_links = _parse_links(str(payload.get("links_text", "")))
    return ctx


async def _build_bootstrap(
    draft: WeeklyResetContext, meta: DraftMeta
) -> dict[str, t.Any]:
    """The page bootstrap JSON: the draft, option pools, toggles and lifecycle flags."""
    config = await load_config()
    return {
        "draft": draft.to_dict(),
        "options": await _build_options(),
        "autopost_enabled": bool(
            await schemas.AutoPostSettings.get_weekly_reset_enabled()
        ),
        "conquest_tiers": list(CONQUEST_TIERS),
        "reward_fields": [list(field) for field in _REWARD_FIELDS],
        # The saved default image (if any), so the form can pre-check "use as default"
        # when this week's image already is the default.
        "default_image_url": config.default_image_url or "",
        # The CV2 container's accent colour, mirrored as the preview's left bar.
        "accent_color": str(cfg.embed_default_color),
        # Whether a post already exists *for the current reset week* (drives which
        # action buttons show: Create-* when there's none, Edit/Delete when there is),
        # and whether that post has been crossposted (hides the Edit-and-publish button
        # once published, and drives the stronger delete-confirm wording).
        "post_this_period": meta.is_current(current_reset_ts()),
        "crossposted": meta.crossposted,
    }


async def _persist_default_image(
    payload: t.Mapping[str, t.Any], ctx: WeeklyResetContext
) -> None:
    """Persist this week's image as the carried-over default when the box is ticked.

    ``build_draft_context`` seeds next week's ``ctx.image_url`` from it; an empty image
    URL with the box ticked clears the default. A no-op when the box is unticked.
    """
    if payload.get("set_default_image"):
        config = await load_config()
        config.default_image_url = ctx.image_url
        await save_config(config)


# The routes are auth-free thin wrappers over the shared hybrid_post_core handlers,
# passing this producer's ``_SPEC`` and live ``_bot`` (read at call time, so a test that
# monkeypatches ``wr._bot`` is honoured). Auth is enforced by the web_auth middleware.
async def _handle_form_get(request: aiohttp.web.Request) -> aiohttp.web.Response:
    return await hybrid_post_core.form_get(_SPEC, request, _bot)


async def _handle_create(request: aiohttp.web.Request) -> aiohttp.web.Response:
    return await hybrid_post_core.post_action(_SPEC, request, _bot, create=True)


async def _handle_edit(request: aiohttp.web.Request) -> aiohttp.web.Response:
    return await hybrid_post_core.post_action(_SPEC, request, _bot, create=False)


async def _handle_preview(request: aiohttp.web.Request) -> aiohttp.web.Response:
    return await hybrid_post_core.preview(_SPEC, request, _bot)


async def _handle_delete(request: aiohttp.web.Request) -> aiohttp.web.Response:
    return await hybrid_post_core.delete(_SPEC, request, _bot)


async def _handle_auto(request: aiohttp.web.Request) -> aiohttp.web.Response:
    return await hybrid_post_core.auto(_SPEC, request, _bot)


#: Wires this producer to the shared hybrid_post_core (built after every hook exists).
_SPEC = HybridPostSpec(
    followable_key="weekly_reset",
    post_noun="weekly-reset post",
    current_reset_ts=_now_reset_ts,
    render=_render_for_spec,
    validate=validate_post,
    build_body=build_body,
    load_draft=load_draft,
    save_draft=save_draft,
    build_context=build_draft_context,
    context_from_payload=_context_from_payload,
    load_meta=load_meta,
    save_meta=save_meta,
    build_bootstrap=_build_bootstrap,
    persist_default_image=_persist_default_image,
    get_autopost=schemas.AutoPostSettings.get_weekly_reset_enabled,
    set_autopost=schemas.AutoPostSettings.set_weekly_reset,
    form_html_path=_FORM_HTML_PATH,
    draft_lock=_draft_lock,
)


def register_weekly_reset_routes(app: aiohttp.web.Application) -> None:
    """Add the weekly-reset web form routes to the shared persistent app."""
    app.router.add_get("/weekly_reset", _handle_form_get)
    app.router.add_post("/weekly_reset/create", _handle_create)
    app.router.add_post("/weekly_reset/edit", _handle_edit)
    app.router.add_post("/weekly_reset/preview", _handle_preview)
    app.router.add_post("/weekly_reset/delete", _handle_delete)
    app.router.add_post("/weekly_reset/auto", _handle_auto)


web.register_routes(register_weekly_reset_routes)
web.register_card(
    web.Card(
        "Weekly Reset",
        "Compose & publish the weekly-reset post",
        "/weekly_reset",
    )
)


# ---------------------------------------------------------------------------
# Slash command — the sole remaining weekly_reset Discord surface
# ---------------------------------------------------------------------------


weekly_reset_group = lb.Group("weekly_reset", "Weekly Reset Overview (owner only)")


@weekly_reset_group.register
class Create(
    lb.SlashCommand,
    name="create",
    description="Open the owner-only weekly-reset web form",
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context) -> None:
        if not cfg.public_base_url:
            await respond_cv2(
                ctx,
                cv2_error(
                    "No editor link available",
                    "No public base URL is configured (set PUBLIC_BASE_URL or run on "
                    "Railway), so I can't mint a reachable edit link.",
                ),
                ephemeral=True,
            )
            return

        url = f"{cfg.public_base_url}/weekly_reset"
        # Ephemeral (owner-private) response with a link button. The form itself is
        # gated by Discord OAuth (web_auth.py) — you sign in with Discord on first open.
        container = cv2_notice(
            "Open the weekly-reset form with the button below — you'll sign in with "
            "Discord the first time. Edit, preview, save, publish and toggle the "
            "autopost all from that page."
        )
        row = h.impl.MessageActionRowBuilder()
        row.add_component(
            h.impl.LinkButtonBuilder(url=url, label="Open weekly-reset form")
        )
        container.add_component(row)
        await respond_cv2(ctx, container, ephemeral=True)


@loader.listener(h.StartedEvent)
async def _schedule_weekly_reset(
    event: h.StartedEvent, bot: CachedFetchBot = lb.di.INJECTED
) -> None:
    if not cfg.followables.get("weekly_reset"):
        return

    # Stash the live bot so the web form's create/edit routes can reach the REST client.
    global _bot
    _bot = bot

    # Prewarm the manifest-backed option-pool indexes so the first form load is fast.
    asyncio.create_task(get_indexes())

    # Tuesday 17:00 UTC weekly reset. Enable/disable lives on the web form's autopost
    # toggle (POST /weekly_reset/auto -> AutoPostSettings.set_weekly_reset).
    @aiocron.crontab("0 17 * * TUE", start=True)
    # Testing: post every minute -> @aiocron.crontab("* * * * *", start=True)
    async def autopost_weekly_reset() -> None:
        if not await schemas.AutoPostSettings.get_weekly_reset_enabled():
            return
        await run_reset_draft(bot, ping_owners=True)


# The web form's routes are always registered (above); the slash command that mints the
# link is gated on the publish target (the weekly_reset followable) — the same gate that
# guards the autopost cron and the StartedEvent listener.
if cfg.followables.get("weekly_reset"):
    loader.command(weekly_reset_group)
