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
   carried-over curated bits, and posts a **draft** (a live Components V2 preview)
   to the team drafts channel (:data:`cfg.drafts_channel`), pinging the
   bot owners.
2. The team opens ``/weekly_reset edit`` — an owner-only, ephemeral interactive editor —
   picks the weapons/rotators/etc. and types the editorial prose.
3. They preview the assembled post and, on confirm, publish it (crossposted) to
   :data:`cfg.followables["weekly_reset"]`; beacon mirrors it as usual.

Everything the API can't supply (editorial prose, the featured raid/dungeon rotators,
Iron Banner/Trials schedule, Pantheon pair) carries over week-to-week in the
``weekly_reset_config`` :class:`~dd.common.schemas.RotationData` row and is editable in
Discord — no web form, no redeploy.

The interactive editor + commands live in the back half of this module; the front
half is pure data: the context model, the persisted config, the Bungie derivations
and the Components V2 renderer.
"""

import asyncio
import contextlib
import dataclasses
import datetime as dt
import json
import logging
import re
import typing as t
import uuid

import aiocron
import aiosqlite
import hikari as h
import lightbulb as lb
from lightbulb import components as lbc

from dd.hmessage import HMessage

from ...common import cfg, schemas
from ...common.bot import CachedFetchBot
from ...common.components import build_container
from ...common.utils import guild_scope
from .. import utils
from ..embeds import substitute_user_side_emoji
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

#: A known Tuesday 17:00 UTC weekly-reset boundary (matches beacon's weekly_reset ref).
REFERENCE_RESET = dt.datetime(2023, 7, 18, 17, tzinfo=dt.UTC)
WEEK = dt.timedelta(days=7)

#: Column separator used in the post body, verbatim from the hand-authored posts.
SEP = "┊"  # ┊
LEGACY_ACTIVITIES_URL = "https://kyberscorner.com/destiny2/legacy-activities/"
SIGN_OFF = "***See you starside!*** \U0001f4ab"
FOOTER = "-# via Destiny Director (Kyber)"
#: Editorial suffix on the Zavala weapon line (tier text is not API-derivable).
ZAVALA_TIER_SUFFIX = "(T5/*rolls vary*)"
TRIALS_IB_REMINDER = (
    "Reminder: Trials of Osiris is unavailable while Iron Banner is active."
)

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


@dataclasses.dataclass
class WeaponRef:
    """A weapon slot: enough to render a light.gg-linked, emoji-prefixed line.

    Derived weapons carry a ``hash`` (so we can deep-link light.gg and infer the
    weapon-type emoji); hand-typed weapons may have no hash (plain text, no link).
    """

    name: str
    hash: int | None = None
    #: weapon-type emoji name (e.g. "pulse_rifle"); only needed for the Zavala line.
    emoji_name: str | None = None

    @property
    def lightgg_url(self) -> str | None:
        return f"https://light.gg/db/items/{self.hash}" if self.hash else None

    def markdown(self) -> str:
        """``[Name](url)`` when we have a hash, else plain ``Name``."""
        url = self.lightgg_url
        return f"[{self.name}]({url})" if url else self.name

    @classmethod
    def from_item(cls, item: "api.DestinyItem") -> "WeaponRef":
        return cls(name=item.name, hash=item.hash, emoji_name=item.expected_emoji_name)

    def to_dict(self) -> dict[str, t.Any]:
        return {"name": self.name, "hash": self.hash, "emoji_name": self.emoji_name}

    @classmethod
    def from_dict(cls, d: t.Mapping[str, t.Any]) -> "WeaponRef":
        return cls(name=d["name"], hash=d.get("hash"), emoji_name=d.get("emoji_name"))


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
    # EVENTS / Trials
    iron_banner: bool = False
    trials_active: bool = True
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
            "iron_banner": self.iron_banner,
            "trials_active": self.trials_active,
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
            iron_banner=bool(d.get("iron_banner", False)),
            trials_active=bool(d.get("trials_active", True)),
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


def current_reset_ts(now: dt.datetime | None = None) -> int:
    """Unix ts of the reset boundary for the week containing ``now`` (Tue 17:00 UTC)."""
    now = now or dt.datetime.now(tz=dt.UTC)
    weeks = (now - REFERENCE_RESET) // WEEK
    return int((REFERENCE_RESET + weeks * WEEK).timestamp())


def next_reset_ts(reset_ts: int) -> int:
    """First reset boundary strictly after ``reset_ts`` — i.e. the next Tuesday.

    ``reset_ts`` is the *current* week's boundary (which drives the rotators), so
    this is the moment the post's content resets, shown on the ``Resets:`` line.
    """
    return reset_ts + int(WEEK.total_seconds())


def rotator_index(anchor_ts: int, reset_ts: int, length: int) -> int:
    """Which cycle entry is active this week (weeks since anchor, mod list length)."""
    if length <= 0:
        return 0
    weeks = (reset_ts - anchor_ts) // int(WEEK.total_seconds())
    return weeks % length


def compute_rotator(
    pairs: t.Sequence[tuple[str, str]], anchor_ts: int, reset_ts: int
) -> tuple[str, str]:
    if not pairs:
        return ("", "")
    return pairs[rotator_index(anchor_ts, reset_ts, len(pairs))]


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

    # EVENTS — only when there is something eventful to say.
    if ctx.iron_banner or ctx.events_narrative:
        lines += ["", "**EVENTS**", ""]
        if ctx.iron_banner:
            lines.append(f":IronBanner: {SEP} Iron Banner has returned!")
            lines += ["", TRIALS_IB_REMINDER]
        if ctx.events_narrative:
            lines += ["", ctx.events_narrative]

    # VANGUARD ALERTS. GM is Portal-derived; Quickplay/Control are the manually-set
    # weekly featured weapons (the API exposes only the daily reward or the full pool).
    lines += ["", "**VANGUARD ALERTS (Seasonal Tab)**", ""]
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

    # Trials window (mutually exclusive with Iron Banner weeks).
    if ctx.trials_active and not ctx.iron_banner:
        lines += [
            "",
            "**From Friday - Tuesday**",
            f":trials: {SEP} 3v3: Trials of Osiris",
        ]

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


def build_cv2(body: str, image_url: str | None) -> HMessage:
    """Wrap an already-emoji-substituted body + optional image in a CV2 HMessage."""
    container = h.impl.ContainerComponentBuilder(accent_color=cfg.embed_default_color)
    container.add_text_display(body)
    if image_url:
        gallery = h.impl.MediaGalleryComponentBuilder()
        gallery.add_media_gallery_item(image_url)
        container.add_component(gallery)
    container.add_separator(divider=True)
    container.add_text_display(FOOTER)
    return HMessage(components=[container])


async def format_weekly_reset(ctx: WeeklyResetContext, bot: CachedFetchBot) -> HMessage:
    """Render the context to a Components V2 :class:`HMessage`."""
    body = await substitute_user_side_emoji(bot, build_body(ctx))
    return build_cv2(body, ctx.image_url)


async def weekly_reset_message_constructor(bot: CachedFetchBot) -> HMessage:
    """Announcer hook: render the current draft (build a fresh one if none is saved)."""
    ctx = await load_draft()
    if ctx is None:
        ctx = await build_draft_context()
    return await format_weekly_reset(ctx, bot)


def activity_record(ctx: WeeklyResetContext) -> dict[str, t.Any]:
    """The activity choices for a published week, as a flat machine-parseable dict."""

    def wname(weapon: WeaponRef | None) -> str | None:
        return weapon.name if weapon else None

    return {
        "reset_ts": ctx.reset_ts,
        "gm_strike": ctx.gm_strike or None,
        "gm_weapon": wname(ctx.gm_weapon),
        "quickplay_weapon": wname(ctx.quickplay_weapon),
        "control_weapon": wname(ctx.control_weapon),
        "zavala_weapon": wname(ctx.zavala_weapon),
        "seasonal_raid": ctx.seasonal_raid or None,
        "seasonal_dungeon": ctx.seasonal_dungeon or None,
        "rotator_raids": [r for r in ctx.rotator_raids if r],
        "rotator_dungeons": [d for d in ctx.rotator_dungeons if d],
        "pantheon_reprise": ctx.pantheon_reprise or None,
        "pantheon_encore": ctx.pantheon_encore or None,
        "crucible_1v6": ctx.crucible_1v6 or None,
        "crucible_3v3": ctx.crucible_3v3 or None,
        "crucible_6v6": ctx.crucible_6v6 or None,
        "iron_banner": ctx.iron_banner,
        "trials_active": ctx.trials_active,
    }


async def record_publish(bot: CachedFetchBot, ctx: WeeklyResetContext) -> None:
    """Log the published activity choices to the records channel for later analysis."""
    channel_id = cfg.weekly_reset_records_channel
    if not channel_id:
        return
    record = json.dumps(activity_record(ctx))
    try:
        await bot.rest.create_message(
            channel_id,
            f"Weekly reset <t:{ctx.reset_ts}:D>\n```json\n{record}\n```",
        )
    except Exception:
        logger.warning("weekly_reset: records post failed", exc_info=True)


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
# Draft metadata (card message id, publish status, "needs attention" flags)
# ---------------------------------------------------------------------------

META_SLUG = "weekly_reset_meta"


@dataclasses.dataclass
class DraftMeta:
    card_channel_id: int = 0
    card_message_id: int = 0
    status: str = "draft"  # "draft" | "published"
    published_message_id: int = 0
    last_edited_by: int = 0
    last_edited_ts: int = 0
    needs_attention: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, t.Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: t.Mapping[str, t.Any] | None) -> "DraftMeta":
        if not d:
            return cls()
        return cls(
            card_channel_id=int(d.get("card_channel_id", 0)),
            card_message_id=int(d.get("card_message_id", 0)),
            status=d.get("status", "draft"),
            published_message_id=int(d.get("published_message_id", 0)),
            last_edited_by=int(d.get("last_edited_by", 0)),
            last_edited_ts=int(d.get("last_edited_ts", 0)),
            needs_attention=list(d.get("needs_attention") or []),
        )


async def load_meta() -> DraftMeta:
    return DraftMeta.from_dict(await schemas.RotationData.get_data(META_SLUG))


async def save_meta(meta: DraftMeta) -> None:
    await schemas.RotationData.set_data(META_SLUG, meta.to_dict())


# ---------------------------------------------------------------------------
# Owner gating + single-writer session control
# ---------------------------------------------------------------------------

#: Serialises read-modify-write of the shared draft doc (single bot process).
_draft_lock = asyncio.Lock()
#: The session id currently allowed to mutate the draft; older sessions are superseded.
_active_session: str | None = None
#: Editor session cap — comfortably inside the 15-min interaction-token TTL so the final
#: "controls disabled" edit still lands; every edit is persisted, so a timeout loses
#: nothing (re-run /weekly_reset edit to resume).
_SESSION_TIMEOUT = 840


async def _is_owner(bot: CachedFetchBot, user_id: int) -> bool:
    return user_id in await bot.fetch_owner_ids()


# ---------------------------------------------------------------------------
# The drafts-channel card = the canonical live preview
# ---------------------------------------------------------------------------


def _card_status_text(meta: DraftMeta) -> str:
    bits: list[str] = []
    if meta.status == "published":
        bits.append("✅ Published")
    else:
        bits.append("📝 Draft")
    if meta.last_edited_by:
        bits.append(
            f"last edit by <@{meta.last_edited_by}> <t:{meta.last_edited_ts}:R>"
        )
    if meta.needs_attention:
        bits.append("⚠️ needs attention: " + ", ".join(meta.needs_attention))
    return "-# " + " · ".join(bits)


async def render_card(
    ctx: WeeklyResetContext, meta: DraftMeta, bot: CachedFetchBot
) -> list[h.api.ComponentBuilder]:
    """Card body: the live post preview + a status line + the how-to-edit hint."""
    preview = await format_weekly_reset(ctx, bot)
    components = list(preview.components)
    container = components[0]
    if isinstance(container, h.impl.ContainerComponentBuilder):
        container.add_separator(divider=True)
        container.add_text_display(_card_status_text(meta))
        container.add_text_display(
            "-# ▶ Run `/weekly_reset edit` to pick, edit and publish this post."
        )
    return components


async def post_or_update_card(
    bot: CachedFetchBot,
    ctx: WeeklyResetContext,
    meta: DraftMeta,
    *,
    ping_owners: bool = False,
) -> DraftMeta:
    """Edit the existing card in place, or post a fresh one (pinging owners once)."""
    channel_id = cfg.drafts_channel
    if not channel_id:
        return meta
    components = await render_card(ctx, meta, bot)

    if meta.card_message_id:
        try:
            await bot.rest.edit_message(
                channel_id, meta.card_message_id, components=components
            )
            return meta
        except Exception:
            logger.warning("weekly_reset: card edit failed; reposting", exc_info=True)

    # Fresh card. Ping the owners with a tiny companion message (a CV2 post has no
    # `content`, so a mention there is unreliable) then post the card itself.
    if ping_owners:
        try:
            owner_ids = await bot.fetch_owner_ids()
            mention = " ".join(f"<@{oid}>" for oid in owner_ids)
            await bot.rest.create_message(
                channel_id,
                f"{mention} 🗓️ Weekly reset draft is ready to review.",
                user_mentions=True,
            )
        except Exception:
            logger.warning("weekly_reset: owner ping failed", exc_info=True)

    posted = await bot.rest.create_message(
        channel_id,
        components=components,
        flags=h.MessageFlag.IS_COMPONENTS_V2,
    )
    meta.card_channel_id = channel_id
    meta.card_message_id = posted.id
    await save_meta(meta)
    return meta


# ---------------------------------------------------------------------------
# Editor: the section model (which selects/modal each section shows)
# ---------------------------------------------------------------------------

# Only the fields NOT covered by the `/weekly_reset set_activity` / `set_reward`
# autocomplete commands live in the editor: the Iron Banner/Trials toggles + prose,
# ad-hoc notes/links, and the header image.
_SECTIONS: tuple[tuple[str, str], ...] = (
    ("events", "Events & Trials"),
    ("notes", "Notes & Links"),
    ("image", "Image"),
)
_SECTION_LABELS = dict(_SECTIONS)

# A select "option" tuple: (label, value, is_default).
Option = tuple[str, str, bool]


def _select_a(ctx: WeeklyResetContext, section: str) -> tuple[str, list[Option]] | None:
    """Placeholder + options for the section's first select, if it has one.

    Weapons/activities are set via the autocomplete ``/weekly_reset set_*`` commands, so
    the only in-editor selects left are the Iron Banner / Trials toggles.
    """
    if section == "events":
        opts = [
            ("Iron Banner: ON", "ib_on", ctx.iron_banner),
            ("Iron Banner: OFF", "ib_off", not ctx.iron_banner),
        ]
        return ("Iron Banner this week?", opts)
    return None


def _select_b(ctx: WeeklyResetContext, section: str) -> tuple[str, list[Option]] | None:
    if section == "events":
        opts = [
            ("Trials line: ON", "trials_on", ctx.trials_active),
            ("Trials line: OFF", "trials_off", not ctx.trials_active),
        ]
        return ("Show the Trials line?", opts)
    return None


def _apply_select_a(ctx: WeeklyResetContext, section: str, value: str) -> None:
    if section == "events":
        ctx.iron_banner = value == "ib_on"
        if ctx.iron_banner:
            ctx.trials_active = False


def _apply_select_b(ctx: WeeklyResetContext, section: str, value: str) -> None:
    if section == "events":
        ctx.trials_active = value == "trials_on"


# A modal field spec: (label, current_value, multiline).
Field = tuple[str, str, bool]


def _modal_spec(
    ctx: WeeklyResetContext, section: str
) -> tuple[str, list[Field]] | None:
    """Title + field specs for the section's free-text modal, if it has one."""
    if section == "events":
        return (
            "Events narrative",
            [("Events prose (optional)", ctx.events_narrative, True)],
        )
    if section == "notes":
        return (
            "Notes & Links",
            [
                ("Info notes (one per line)", "\n".join(ctx.notes), True),
                (
                    "Extra links — 'Label | https://url' per line",
                    _links_text(ctx),
                    True,
                ),
            ],
        )
    if section == "image":
        return (
            "Header image",
            [("Image URL (blank to clear)", ctx.image_url or "", False)],
        )
    return None


def _apply_modal(ctx: WeeklyResetContext, section: str, values: list[str]) -> None:
    if section == "events":
        ctx.events_narrative = (values[0] if values else "").strip()
    elif section == "notes":
        notes_raw = values[0] if values else ""
        links_raw = values[1] if len(values) > 1 else ""
        ctx.notes = [line.strip() for line in notes_raw.splitlines() if line.strip()]
        ctx.extra_links = _parse_links(links_raw)
    elif section == "image":
        url = (values[0] if values else "").strip()
        ctx.image_url = url or None


def _links_text(ctx: WeeklyResetContext) -> str:
    return "\n".join(
        f"{link.get('label', '')} | {link.get('url', '')}" for link in ctx.extra_links
    )


def _parse_links(raw: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for line in raw.splitlines():
        if "|" not in line:
            continue
        label, url = (part.strip() for part in line.split("|", 1))
        if label and url.startswith(("http://", "https://")):
            links.append({"label": label, "url": url})
    return links


# ---------------------------------------------------------------------------
# Editor session + rendering
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _Session:
    session_id: str
    invoker_id: int
    ctx: WeeklyResetContext
    meta: DraftMeta
    section: str = "events"
    confirm: bool = False


def _summary(ctx: WeeklyResetContext, section: str) -> str:
    """Compact view of the current section's values (the full preview is the card)."""
    if section == "events":
        return (
            f"Iron Banner: {'ON' if ctx.iron_banner else 'OFF'}\n"
            f"Trials line: {'ON' if ctx.trials_active else 'OFF'}\n"
            f"Prose: {ctx.events_narrative or '—'}"
        )
    if section == "notes":
        return f"Notes: {len(ctx.notes)}\nExtra links: {len(ctx.extra_links)}"
    if section == "image":
        return f"Image: {ctx.image_url or '—'}"
    return ""


def _render_editor(session: _Session) -> list[h.api.ComponentBuilder]:
    sid = session.session_id
    ctx = session.ctx
    section = session.section

    if session.confirm:
        republish = bool(session.meta.published_message_id)
        container = build_container(
            [
                "## Update the published post?"
                if republish
                else "## Publish weekly reset?",
                (
                    "This edits the already-published post in place; beacon re-mirrors "
                    "the change to every follower."
                    if republish
                    else "This posts **exactly the card** in the drafts channel and "
                    "crossposts it; beacon mirrors it to every follower."
                ),
            ]
        )
        row = h.impl.MessageActionRowBuilder()
        row.add_interactive_button(
            h.ButtonStyle.SUCCESS,
            f"{sid}:confirm",
            label="Confirm update" if republish else "Confirm publish",
        )
        row.add_interactive_button(h.ButtonStyle.SECONDARY, f"{sid}:back", label="Back")
        return [container, row]

    container = build_container(
        [
            "## Weekly Reset — editor",
            f"Editing: **{_SECTION_LABELS[section]}**\n{_summary(ctx, section)}",
            "-# The full live preview is the draft card in the drafts channel.",
        ]
    )
    components: list[h.api.ComponentBuilder] = [container]

    # Section picker.
    section_row = h.impl.MessageActionRowBuilder()
    section_menu = section_row.add_text_menu(
        f"{sid}:section", placeholder="Jump to a section…"
    )
    for key, label in _SECTIONS:
        section_menu.add_option(label, key, is_default=key == section)
    components.append(section_row)

    # Section-specific selects.
    spec_a = _select_a(ctx, section)
    if spec_a:
        placeholder, options = spec_a
        row_a = h.impl.MessageActionRowBuilder()
        menu_a = row_a.add_text_menu(f"{sid}:pick_a", placeholder=placeholder)
        for label, value, default in options:
            menu_a.add_option(label, value, is_default=default)
        components.append(row_a)
    spec_b = _select_b(ctx, section)
    if spec_b:
        placeholder, options = spec_b
        row_b = h.impl.MessageActionRowBuilder()
        menu_b = row_b.add_text_menu(f"{sid}:pick_b", placeholder=placeholder)
        for label, value, default in options:
            menu_b.add_option(label, value, is_default=default)
        components.append(row_b)

    # Action buttons: edit this section's text, save & close, or go to publish.
    buttons = h.impl.MessageActionRowBuilder()
    if _modal_spec(ctx, section):
        buttons.add_interactive_button(
            h.ButtonStyle.PRIMARY, f"{sid}:text", label="✏️ Edit text"
        )
    buttons.add_interactive_button(
        h.ButtonStyle.SECONDARY, f"{sid}:save", label="💾 Save & close"
    )
    buttons.add_interactive_button(
        h.ButtonStyle.SUCCESS, f"{sid}:publish", label="📣 Publish…"
    )
    components.append(buttons)
    return components


class _FieldsModal(lbc.Modal):
    """Generic free-text modal; on submit it applies values and re-renders."""

    def __init__(
        self,
        field_specs: list[Field],
        on_apply: t.Callable[[list[str]], t.Awaitable[list[h.api.ComponentBuilder]]],
    ) -> None:
        self._inputs: list[lbc.TextInput] = []
        for label, value, multiline in field_specs:
            add = (
                self.add_paragraph_text_input
                if multiline
                else self.add_short_text_input
            )
            self._inputs.append(
                add(label[:45], value=value or h.UNDEFINED, required=False)
            )
        self._on_apply = on_apply

    async def on_submit(self, ctx: lbc.ModalContext) -> None:
        values = [ctx.value_for(field) or "" for field in self._inputs]
        await ctx.interaction.create_initial_response(
            h.ResponseType.DEFERRED_MESSAGE_UPDATE
        )
        components = await self._on_apply(values)
        await ctx.interaction.edit_initial_response(components=components)


async def open_editor(ctx: lb.Context, bot: CachedFetchBot) -> None:
    """Open the owner-only, ephemeral interactive editor (canonical entry point)."""
    global _active_session

    draft = await load_draft()
    if draft is None:
        draft = await build_draft_context()
        await save_draft(draft)
    meta = await load_meta()

    session = _Session(
        session_id=uuid.uuid4().hex[:8],
        invoker_id=ctx.user.id,
        ctx=draft,
        meta=meta,
    )
    _active_session = session.session_id

    async def _mutate(fn: t.Callable[[WeeklyResetContext], None]) -> bool:
        """Apply ``fn`` to the draft under the single-writer lock, then persist.

        Returns False (and does nothing) if this session has been superseded.
        """
        async with _draft_lock:
            if _active_session != session.session_id:
                return False
            # Pick up any out-of-band edits (e.g. the /weekly_reset set_* commands)
            # before applying this one, so concurrent writers don't clobber.
            latest = await load_draft()
            if latest is not None:
                session.ctx = latest
            fn(session.ctx)
            session.meta.status = "draft"
            session.meta.last_edited_by = session.invoker_id
            session.meta.last_edited_ts = int(dt.datetime.now(tz=dt.UTC).timestamp())
            await save_draft(session.ctx)
            await save_meta(session.meta)
        await post_or_update_card(bot, session.ctx, session.meta)
        return True

    async def _rerender(mctx: lbc.MenuContext) -> None:
        await mctx.respond(
            edit=True,
            flags=h.MessageFlag.IS_COMPONENTS_V2,
            components=_render_editor(session),
        )

    async def _superseded(mctx: lbc.MenuContext) -> None:
        await mctx.respond(
            edit=True,
            flags=h.MessageFlag.IS_COMPONENTS_V2,
            components=[
                build_container(
                    ["⚠️ Another editor session took over — this one is now read-only."]
                )
            ],
        )
        mctx.stop_interacting()

    async def on_section(mctx: lbc.MenuContext) -> None:
        session.section = mctx.interaction.values[0]
        session.confirm = False
        await _rerender(mctx)

    async def on_pick_a(mctx: lbc.MenuContext) -> None:
        value = mctx.interaction.values[0]
        if not await _mutate(lambda c: _apply_select_a(c, session.section, value)):
            await _superseded(mctx)
            return
        await _rerender(mctx)

    async def on_pick_b(mctx: lbc.MenuContext) -> None:
        value = mctx.interaction.values[0]
        if not await _mutate(lambda c: _apply_select_b(c, session.section, value)):
            await _superseded(mctx)
            return
        await _rerender(mctx)

    async def on_text(mctx: lbc.MenuContext) -> None:
        spec = _modal_spec(session.ctx, session.section)
        if spec is None:
            return
        title, fields = spec

        async def apply(values: list[str]) -> list[h.api.ComponentBuilder]:
            ok = await _mutate(lambda c: _apply_modal(c, session.section, values))
            return (
                _render_editor(session)
                if ok
                else [build_container(["⚠️ Session superseded — nothing changed."])]
            )

        modal = _FieldsModal(fields, apply)
        modal_cid = f"wr_modal:{uuid.uuid4().hex}"
        await mctx.respond_with_modal(title, modal_cid, components=modal)
        with contextlib.suppress(asyncio.TimeoutError):
            await modal.attach(mctx.client, modal_cid, timeout=300)

    async def on_publish(mctx: lbc.MenuContext) -> None:
        problems = validate_post(session.ctx)
        if problems:
            await mctx.respond(
                "Can't publish yet:\n- " + "\n- ".join(problems), ephemeral=True
            )
            return
        session.confirm = True
        await _rerender(mctx)

    async def on_back(mctx: lbc.MenuContext) -> None:
        session.confirm = False
        await _rerender(mctx)

    async def on_confirm(mctx: lbc.MenuContext) -> None:
        global _active_session
        async with _draft_lock:
            if _active_session != session.session_id:
                await _superseded(mctx)
                return
            problems = validate_post(session.ctx)
            if problems:
                session.confirm = False
                await _rerender(mctx)
                return
            hmessage = await format_weekly_reset(session.ctx, bot)
            channel_id = cfg.followables["weekly_reset"]
            initial_publish = not session.meta.published_message_id
            if session.meta.published_message_id:
                # Already published this week: edit the post in place so fixes reach
                # followers via beacon's edit reconciliation, no duplicate post.
                await bot.rest.edit_message(
                    channel_id,
                    session.meta.published_message_id,
                    components=hmessage.components,
                )
                note = "✏️ Updated the published post — beacon re-mirrors the edit."
            else:
                posted = await utils.send_message(
                    bot, hmessage, channel_id, crosspost=True
                )
                session.meta.published_message_id = posted.id
                note = "✅ Published and crossposted — beacon will mirror it out."
            session.meta.status = "published"
            await save_meta(session.meta)
            _active_session = None
        # Log the activity choices once per week (first publish) for later analysis.
        if initial_publish:
            await record_publish(bot, session.ctx)
        await post_or_update_card(bot, session.ctx, session.meta)
        await mctx.respond(
            edit=True,
            flags=h.MessageFlag.IS_COMPONENTS_V2,
            components=[build_container([note])],
        )
        mctx.stop_interacting()

    async def on_save(mctx: lbc.MenuContext) -> None:
        global _active_session
        async with _draft_lock:
            if _active_session == session.session_id:
                await save_draft(session.ctx)
                _active_session = None
        await post_or_update_card(bot, session.ctx, session.meta)
        await mctx.respond(
            edit=True,
            flags=h.MessageFlag.IS_COMPONENTS_V2,
            components=[
                build_container(
                    ["💾 Saved — the drafts card is up to date. Not published yet."]
                )
            ],
        )
        mctx.stop_interacting()

    menu = lbc.Menu()
    menu.add_text_select(["_"], on_section, custom_id=f"{session.session_id}:section")
    menu.add_text_select(["_"], on_pick_a, custom_id=f"{session.session_id}:pick_a")
    menu.add_text_select(["_"], on_pick_b, custom_id=f"{session.session_id}:pick_b")
    menu.add_interactive_button(
        h.ButtonStyle.PRIMARY,
        on_text,
        custom_id=f"{session.session_id}:text",
        label="✏️",
    )
    menu.add_interactive_button(
        h.ButtonStyle.SECONDARY,
        on_save,
        custom_id=f"{session.session_id}:save",
        label="Save",
    )
    menu.add_interactive_button(
        h.ButtonStyle.SUCCESS,
        on_publish,
        custom_id=f"{session.session_id}:publish",
        label="Publish",
    )
    menu.add_interactive_button(
        h.ButtonStyle.SUCCESS,
        on_confirm,
        custom_id=f"{session.session_id}:confirm",
        label="Confirm",
    )
    menu.add_interactive_button(
        h.ButtonStyle.SECONDARY,
        on_back,
        custom_id=f"{session.session_id}:back",
        label="Back",
    )

    await ctx.respond(
        flags=h.MessageFlag.IS_COMPONENTS_V2 | h.MessageFlag.EPHEMERAL,
        components=_render_editor(session),
    )
    with contextlib.suppress(asyncio.TimeoutError):
        await menu.attach(ctx.client, timeout=_SESSION_TIMEOUT)


# ---------------------------------------------------------------------------
# Reset-day cron + commands
# ---------------------------------------------------------------------------


async def run_reset_draft(bot: CachedFetchBot, *, ping_owners: bool) -> None:
    """Build a fresh draft from API + config and (re)post the drafts card."""
    global _active_session
    config = await load_config()
    ctx = await build_draft_context(config)

    async with _draft_lock:
        # A new reset supersedes any in-flight editor session for the old week.
        _active_session = None
        meta = DraftMeta(
            status="draft",
            last_edited_ts=int(dt.datetime.now(tz=dt.UTC).timestamp()),
        )
        # Keep the existing card (if this is a rebuild of the same week) so we edit in
        # place rather than spamming a new card.
        existing = await load_meta()
        if existing.card_message_id and existing.status != "published":
            meta.card_channel_id = existing.card_channel_id
            meta.card_message_id = existing.card_message_id
        await save_draft(ctx)
        await save_meta(meta)

    await post_or_update_card(
        bot, ctx, meta, ping_owners=ping_owners and not meta.card_message_id
    )


weekly_reset_group = lb.Group(
    "weekly_reset", "Build and publish the Weekly Reset Overview post"
)


@weekly_reset_group.register
class WeeklyResetAuto(
    lb.SlashCommand,
    name="auto",
    description="Enable/disable the automatic reset-day draft",
):
    option = lb.string(
        "option",
        "Enable or disable",
        choices=[lb.Choice("Enable", "Enable"), lb.Choice("Disable", "Disable")],
    )

    @lb.invoke
    async def invoke(self, ctx: lb.Context) -> None:
        enable = self.option.lower() == "enable"
        await schemas.AutoPostSettings.set_weekly_reset(enable)
        await ctx.respond(
            f"Weekly-reset auto-draft {'enabled' if enable else 'disabled'}.",
            ephemeral=True,
        )


@weekly_reset_group.register
class WeeklyResetDraft(
    lb.SlashCommand,
    name="draft",
    description="Fetch the API now and (re)build the draft card",
):
    @lb.invoke
    async def invoke(
        self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED
    ) -> None:
        await ctx.defer(ephemeral=True)
        await run_reset_draft(bot, ping_owners=False)
        await ctx.respond("Draft rebuilt — see the drafts channel.", ephemeral=True)


@weekly_reset_group.register
class WeeklyResetEdit(
    lb.SlashCommand,
    name="edit",
    description="Open the interactive editor to pick, edit and publish",
):
    @lb.invoke
    async def invoke(
        self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED
    ) -> None:
        await open_editor(ctx, bot)


@weekly_reset_group.register
class WeeklyResetShow(
    lb.SlashCommand, name="show", description="Preview the current draft (ephemeral)"
):
    @lb.invoke
    async def invoke(
        self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED
    ) -> None:
        hmessage = await weekly_reset_message_constructor(bot)
        await ctx.respond(
            flags=h.MessageFlag.IS_COMPONENTS_V2 | h.MessageFlag.EPHEMERAL,
            components=hmessage.components,
        )


# ---------------------------------------------------------------------------
# Autocomplete-driven set commands
#
# Discord autocomplete only exists on slash-command options, and its responses are
# dispatched *before* the CHECKS pipeline — so they are NOT owner-gated. That is fine
# here by design: the suggestions leak to anyone, but the invoke (the actual write) is
# still gated by the client-level ``owner_only`` hook — only writes are restricted.
# ---------------------------------------------------------------------------

# Reward field -> the DestinyItem itemType its value autocompletes over (3=weapon,
# 2=armour). Every reward slot today is a weapon, so only weapons are suggested; an
# armour slot would set 2 here and get only armour.
_REWARD_ITEM_TYPE: dict[str, int] = {
    "gm_weapon": 3,
    "quickplay_weapon": 3,
    "control_weapon": 3,
    "zavala_weapon": 3,
}
_REWARD_FIELDS: tuple[tuple[str, str], ...] = (
    ("GM Nightfall reward weapon", "gm_weapon"),
    ("Vanguard / Quickplay weapon", "quickplay_weapon"),
    ("Crucible / Control weapon", "control_weapon"),
    ("Zavala's Weapon", "zavala_weapon"),
)
_REWARD_FIELD_CHOICES = [lb.Choice(label, key) for label, key in _REWARD_FIELDS]
# Bounded-selector Choice lists (label == value) for the dedicated set_* commands.
# (Crucible modes exceed 25, so they use autocomplete instead — _crucible_autocomplete.)
_RAID_CHOICES = [lb.Choice(r, r) for r in RAIDS]
_DUNGEON_CHOICES = [lb.Choice(d, d) for d in DUNGEONS]
_PANTHEON_CHOICES = [lb.Choice(b, b) for b in PANTHEON_BOSSES]

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


@dataclasses.dataclass
class _Indexes:
    """Manifest-derived autocomplete data, built once and cached."""

    #: (name, hash, itemTypeDisplayName, itemType, rarity) per weapon/armour, deduped.
    items: list[tuple[str, int, str, int, str]]
    #: category ("raid"/"dungeon"/"strike"/"pantheon"/"crucible") -> sorted names.
    activities: dict[str, list[str]]


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
    # Deduped by (name, type): one entry per named weapon/armour, newest hash wins.
    item_by_key: dict[tuple[str, str], tuple[str, int, str, int, str]] = {}
    # Only GM strikes need manifest autocomplete now; raids/dungeons/pantheon/crucible
    # are bounded Choice selectors (see the *_CHOICES constants).
    strikes: set[str] = set()
    try:
        path = await api._get_latest_manifest(schemas.BungieCredentials.api_key)
        async with aiosqlite.connect(path) as con:
            cur = await con.cursor()

            await cur.execute("SELECT json FROM DestinyInventoryItemDefinition")
            for (row,) in await cur.fetchall():
                defn = json.loads(row)
                item_type = defn.get("itemType")
                if item_type not in (2, 3) or defn.get("redacted"):
                    continue
                rarity = (defn.get("inventory") or {}).get("tierTypeName", "")
                if rarity in ("", "Common", "Basic"):  # drop dummies/whites/greens
                    continue
                name = (defn.get("displayProperties") or {}).get("name")
                if not name:
                    continue
                type_name = defn.get("itemTypeDisplayName", "")
                hash_ = int(defn["hash"])
                key = (name.lower(), type_name.lower())
                existing = item_by_key.get(key)
                if existing is None or hash_ > existing[1]:  # keep the newest hash
                    item_by_key[key] = (name, hash_, type_name, item_type, rarity)

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
                type_name = activity_types.get(defn.get("activityTypeHash"), "")
                if _classify_activity(defn, type_name) != "strike":
                    continue
                name = _clean_activity_name(
                    (defn.get("displayProperties") or {}).get("name", ""), "strike"
                )
                if name:
                    strikes.add(name)
    except Exception:
        logger.warning("weekly_reset: manifest index build failed", exc_info=True)

    result = _Indexes(
        items=sorted(item_by_key.values(), key=lambda e: e[0].lower()),
        activities={"strike": sorted(strikes)},
    )
    logger.info(
        "weekly_reset indexes: %d items; strikes=%d",
        len(result.items),
        len(result.activities["strike"]),
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


async def _gm_strike_autocomplete(ctx: "lb.AutocompleteContext[str]") -> None:
    # GM strike pool (~46 strikes + battlegrounds) is too large for a Choice selector,
    # so it stays on manifest autocomplete. Blank until a character is typed.
    query = str(ctx.focused.value or "").lower()
    if not query or _indexes is None:
        if _indexes is None:
            asyncio.create_task(get_indexes())  # warm for the next keystroke
        await ctx.respond([])
        return
    names = _indexes.activities.get("strike", [])
    await ctx.respond([name for name in names if query in name.lower()][:25])


async def _crucible_autocomplete(ctx: "lb.AutocompleteContext[str]") -> None:
    # >25 modes (base + Labs), so autocomplete rather than a Choice selector. Show the
    # whole list on an empty query (it's short) and filter as the user types.
    query = str(ctx.focused.value or "").lower()
    matches = [m for m in CRUCIBLE_MODES if query in m.lower()]
    await ctx.respond(matches[:25])


async def _reward_value_autocomplete(ctx: "lb.AutocompleteContext[str]") -> None:
    field_opt = ctx.get_option("field")
    field = str(field_opt.value) if field_opt and field_opt.value else ""
    allowed = _REWARD_ITEM_TYPE.get(field, 3)  # weapons-only by default
    if _indexes is None:
        asyncio.create_task(get_indexes())  # warm for the next keystroke
        await ctx.respond([])
        return
    query = str(ctx.focused.value or "").lower()
    if not query:
        await ctx.respond([])
        return
    choices: dict[str, str] = {}
    for name, hash_, type_name, item_type, rarity in _indexes.items:
        if item_type != allowed or query not in name.lower():
            continue
        suffix = " · ".join(part for part in (type_name, rarity) if part)
        label = f"{name} — {suffix}" if suffix else name
        choices[label[:100]] = str(hash_)  # value = hash, so the pick is unambiguous
        if len(choices) >= 25:
            break
    await ctx.respond(choices)


async def resolve_reward_value(value: str) -> WeaponRef | None:
    """A hash (picked from autocomplete) -> full WeaponRef; else a plain typed name."""
    value = value.strip()
    if not value:
        return None
    indexes = await get_indexes()
    if value.isdigit():
        wanted = int(value)
        for name, hash_, type_name, _item_type, _rarity in indexes.items:
            if hash_ == wanted:
                return WeaponRef(name, hash_, api.likely_emoji_name(type_name))
    for name, hash_, type_name, _item_type, _rarity in indexes.items:
        if name.lower() == value.lower():
            return WeaponRef(name, hash_, api.likely_emoji_name(type_name))
    return WeaponRef(name=value)


def apply_gm_strike(ctx: WeeklyResetContext, value: str) -> None:
    ctx.gm_strike = value


def apply_crucible(ctx: WeeklyResetContext, three: str, six: str) -> None:
    """First mode of each slot is fixed; only the featured (second) mode is chosen."""
    if three:
        ctx.crucible_3v3 = f"{CRUCIBLE_3V3_FIRST}, {three}"
    if six:
        ctx.crucible_6v6 = f"{CRUCIBLE_6V6_FIRST}, {six}"


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


async def mutate_draft(
    bot: CachedFetchBot,
    invoker_id: int,
    fn: t.Callable[[WeeklyResetContext], None],
) -> None:
    """Load-modify-save the persisted draft under the lock, then refresh the card."""
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
    await post_or_update_card(bot, draft, meta)


@weekly_reset_group.register
class WeeklyResetSetGmStrike(
    lb.SlashCommand,
    name="set_gm_strike",
    description="Set the GM Nightfall strike (autocomplete)",
):
    value = lb.string(
        "value",
        "Start typing a strike / battleground name",
        autocomplete=_gm_strike_autocomplete,
    )

    @lb.invoke
    async def invoke(
        self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED
    ) -> None:
        await ctx.defer(ephemeral=True)  # first edit may auto-fill from the API
        value = self.value.strip()
        await mutate_draft(bot, ctx.user.id, lambda c: apply_gm_strike(c, value))
        await ctx.respond(
            f"Set **GM Nightfall strike** → {value or '—'}", ephemeral=True
        )


@weekly_reset_group.register
class WeeklyResetSetCrucible(
    lb.SlashCommand,
    name="set_crucible",
    description="Set the Crucible featured modes (3v3=Competitive+…, 6v6=Control+…)",
):
    three_v_three = lb.string(
        "three_v_three",
        "3v3 featured mode (paired with Competitive)",
        autocomplete=_crucible_autocomplete,
        default="",
    )
    six_v_six = lb.string(
        "six_v_six",
        "6v6 featured mode (paired with Control)",
        autocomplete=_crucible_autocomplete,
        default="",
    )

    @lb.invoke
    async def invoke(
        self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED
    ) -> None:
        await ctx.defer(ephemeral=True)
        three, six = self.three_v_three, self.six_v_six
        await mutate_draft(bot, ctx.user.id, lambda c: apply_crucible(c, three, six))
        done = []
        if three:
            done.append(f"3v3 → {CRUCIBLE_3V3_FIRST}, {three}")
        if six:
            done.append(f"6v6 → {CRUCIBLE_6V6_FIRST}, {six}")
        await ctx.respond("Set " + ("; ".join(done) or "nothing"), ephemeral=True)


@weekly_reset_group.register
class WeeklyResetSetPantheon(
    lb.SlashCommand,
    name="set_pantheon",
    description="Set the Pantheon Reprise / Encore bosses",
):
    reprise = lb.string(
        "reprise", "Reprise boss", choices=_PANTHEON_CHOICES, default=""
    )
    encore = lb.string("encore", "Encore boss", choices=_PANTHEON_CHOICES, default="")

    @lb.invoke
    async def invoke(
        self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED
    ) -> None:
        await ctx.defer(ephemeral=True)
        reprise, encore = self.reprise, self.encore
        await mutate_draft(
            bot, ctx.user.id, lambda c: apply_pantheon(c, reprise, encore)
        )
        done = []
        if reprise:
            done.append(f"Reprise → {reprise}")
        if encore:
            done.append(f"Encore → {encore}")
        await ctx.respond("Set " + ("; ".join(done) or "nothing"), ephemeral=True)


@weekly_reset_group.register
class WeeklyResetSetRaid(
    lb.SlashCommand,
    name="set_raid",
    description="Set the seasonal / featured-rotator raids",
):
    seasonal = lb.string(
        "seasonal", "Seasonal featured raid", choices=_RAID_CHOICES, default=""
    )
    featured_1 = lb.string(
        "featured_1", "Featured rotator raid 1", choices=_RAID_CHOICES, default=""
    )
    featured_2 = lb.string(
        "featured_2", "Featured rotator raid 2", choices=_RAID_CHOICES, default=""
    )

    @lb.invoke
    async def invoke(
        self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED
    ) -> None:
        await ctx.defer(ephemeral=True)
        seasonal, feat1, feat2 = self.seasonal, self.featured_1, self.featured_2
        await mutate_draft(
            bot, ctx.user.id, lambda c: apply_raids(c, seasonal, feat1, feat2)
        )
        await ctx.respond("Updated the raids.", ephemeral=True)


@weekly_reset_group.register
class WeeklyResetSetDungeon(
    lb.SlashCommand,
    name="set_dungeon",
    description="Set the seasonal / featured-rotator dungeons",
):
    seasonal = lb.string(
        "seasonal", "Seasonal featured dungeon", choices=_DUNGEON_CHOICES, default=""
    )
    featured_1 = lb.string(
        "featured_1", "Featured rotator dungeon 1", choices=_DUNGEON_CHOICES, default=""
    )
    featured_2 = lb.string(
        "featured_2", "Featured rotator dungeon 2", choices=_DUNGEON_CHOICES, default=""
    )

    @lb.invoke
    async def invoke(
        self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED
    ) -> None:
        await ctx.defer(ephemeral=True)
        seasonal, feat1, feat2 = self.seasonal, self.featured_1, self.featured_2
        await mutate_draft(
            bot, ctx.user.id, lambda c: apply_dungeons(c, seasonal, feat1, feat2)
        )
        await ctx.respond("Updated the dungeons.", ephemeral=True)


@weekly_reset_group.register
class WeeklyResetSetReward(
    lb.SlashCommand,
    name="set_reward",
    description="Set a weapon/armour reward (autocompletes Destiny items)",
):
    field = lb.string("field", "Which reward slot", choices=_REWARD_FIELD_CHOICES)
    value = lb.string(
        "value",
        "Search weapons/armour, or type a name (blank to clear)",
        autocomplete=_reward_value_autocomplete,
    )

    @lb.invoke
    async def invoke(
        self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED
    ) -> None:
        await ctx.defer(
            ephemeral=True
        )  # index build / first-edit auto-fill can be slow
        field = self.field
        weapon = await resolve_reward_value(self.value)
        await mutate_draft(
            bot, ctx.user.id, lambda c: apply_reward_field(c, field, weapon)
        )
        if weapon and weapon.hash:
            shown = f"[{weapon.name}]({weapon.lightgg_url})"
        elif weapon:
            shown = weapon.name
        else:
            shown = "—"
        await ctx.respond(f"Set **{field}** → {shown}", ephemeral=True)


@loader.listener(h.StartedEvent)
async def _schedule_weekly_reset(
    event: h.StartedEvent, bot: CachedFetchBot = lb.di.INJECTED
) -> None:
    if not cfg.drafts_channel:
        return

    # Prewarm the manifest-backed autocomplete indexes so the first keystroke is fast.
    asyncio.create_task(get_indexes())

    # Tuesday 17:00 UTC weekly reset.
    @aiocron.crontab("0 17 * * TUE", start=True)
    # Testing: post every minute -> @aiocron.crontab("* * * * *", start=True)
    async def autopost_weekly_reset() -> None:
        if not await schemas.AutoPostSettings.get_weekly_reset_enabled():
            return
        await run_reset_draft(bot, ping_owners=True)


if cfg.drafts_channel:
    loader.command(
        weekly_reset_group,
        guilds=guild_scope(
            *cfg.test_env,
            cfg.control_discord_server_id,
            cfg.kyber_discord_server_id,
        ),
    )
else:
    logger.info(
        "Weekly-reset autopost dormant: set DRAFTS_CHANNEL_ID to enable "
        "the /weekly_reset commands + reset-day draft."
    )
