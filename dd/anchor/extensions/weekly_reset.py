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
   to the team drafts channel (:data:`cfg.weekly_reset_drafts_channel`), pinging the
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
import typing as t
import uuid

import aiocron
import aiohttp
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
    xur,
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

# Scored Nightfall mode id (DestinyActivityModeType), used to spot the weekly Nightfall.
NIGHTFALL_MODE_TYPE = 46
#: Commander Zavala vendor hash (the weekly featured Nightfall/Vanguard weapons).
ZAVALA_VENDOR_HASH = 69482069

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
DEFAULT_ROTATOR_ANCHOR = 1782234000  # 2026-06-23 17:00 UTC reset (first sampled week)
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
    # VANGUARD ALERTS
    gm_strike: str = ""
    gm_weapon: WeaponRef | None = None
    quickplay_weapon: WeaponRef | None = None
    control_weapon: WeaponRef | None = None
    seasonal_raid: str = ""
    seasonal_dungeon: str = ""
    # ZAVALA'S WEAPON — picked by hand from `zavala_options` (API-returned list).
    zavala_weapon: WeaponRef | None = None
    zavala_options: list[WeaponRef] = dataclasses.field(default_factory=list)
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
            "zavala_options": [w.to_dict() for w in self.zavala_options],
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
            seasonal_raid=d.get("seasonal_raid", ""),
            seasonal_dungeon=d.get("seasonal_dungeon", ""),
            zavala_weapon=weapon("zavala_weapon"),
            zavala_options=[
                WeaponRef.from_dict(w) for w in d.get("zavala_options") or []
            ],
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

    seasonal_raid: str = ""
    seasonal_dungeon: str = ""
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
            seasonal_raid=d.get("seasonal_raid", ""),
            seasonal_dungeon=d.get("seasonal_dungeon", ""),
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


async def _resolve_activity(
    session: aiohttp.ClientSession,
    activity_hash: int,
    cache: dict[tuple[str, int], dict[str, t.Any] | None],
) -> dict[str, t.Any] | None:
    """Live per-hash DestinyActivityDefinition lookup (reuses portal_ops' resolver)."""
    return await portal_ops._resolve_entity(
        session, "DestinyActivityDefinition", activity_hash, cache
    )


async def derive_gm_nightfall(session: aiohttp.ClientSession) -> str:
    """Best-effort weekly Nightfall strike name from GetPublicMilestones.

    Returns "" on any problem; the reward weapon is not in the public API, so the team
    supplies it (or it comes from the Zavala vendor options).
    """
    try:
        milestones = await api.client.fetch_public_milestones(session)
    except Exception:
        logger.warning("weekly_reset: GetPublicMilestones failed", exc_info=True)
        return ""

    cache: dict[tuple[str, int], dict[str, t.Any] | None] = {}
    best_name = ""
    for milestone in milestones.values():
        if not isinstance(milestone, dict):
            continue
        for activity in milestone.get("activities", []) or []:
            activity_hash = activity.get("activityHash")
            if not activity_hash:
                continue
            try:
                defn = await _resolve_activity(session, activity_hash, cache)
            except Exception:
                continue
            if not defn:
                continue
            if defn.get("directActivityModeType") != NIGHTFALL_MODE_TYPE:
                continue
            name = (defn.get("originalDisplayProperties") or {}).get("name") or (
                defn.get("displayProperties") or {}
            ).get("name", "")
            if not name:
                continue
            # Prefer the Grandmaster tier when present; otherwise keep the first.
            if "grandmaster" in name.lower():
                return _strip_nightfall_prefix(name)
            best_name = best_name or _strip_nightfall_prefix(name)
    return best_name


def _strip_nightfall_prefix(name: str) -> str:
    for prefix in ("Grandmaster: ", "Nightfall: "):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


async def derive_playlist_weapons() -> tuple[WeaponRef | None, WeaponRef | None]:
    """(quickplay, control) featured weapons via portal_ops' component-204 read."""
    try:
        ops = await portal_ops.fetch_portal_ops()
    except Exception:
        logger.warning("weekly_reset: fetch_portal_ops failed", exc_info=True)
        return (None, None)

    def first_for(tab: str) -> WeaponRef | None:
        for op in ops:
            if op.tab == tab and op.reward_hash:
                return WeaponRef(name=op.reward_name, hash=op.reward_hash)
        return None

    return (first_for("Fireteam Ops"), first_for("Crucible"))


async def derive_zavala_options() -> list[WeaponRef]:
    """Legendary weapons Zavala is featuring this week (team picks one)."""
    try:
        vendor = await xur.fetch_vendor_data(
            api.get_webserver_runner(), [ZAVALA_VENDOR_HASH], "Titan"
        )
    except Exception:
        logger.warning("weekly_reset: Zavala vendor fetch failed", exc_info=True)
        return []
    return [
        WeaponRef.from_item(item)
        for item in vendor.sale_items
        if item.is_weapon and item.is_legendary
    ]


async def build_draft_context(
    config: WeeklyResetConfig | None = None,
) -> WeeklyResetContext:
    """Assemble a fresh draft: compute + best-effort API + carried-over config."""
    config = config or await load_config()
    reset_ts = current_reset_ts()

    ctx = WeeklyResetContext(reset_ts=reset_ts)
    # Carried-over / deterministic fields.
    ctx.seasonal_raid = config.seasonal_raid
    ctx.seasonal_dungeon = config.seasonal_dungeon
    ctx.rotator_raids = compute_rotator(
        config.raid_pairs, config.rotator_anchor, reset_ts
    )
    ctx.rotator_dungeons = compute_rotator(
        config.dungeon_pairs, config.rotator_anchor, reset_ts
    )
    ctx.pantheon_reprise = config.last_pantheon_reprise
    ctx.pantheon_encore = config.last_pantheon_encore
    ctx.crucible_1v6 = config.crucible_1v6
    ctx.crucible_3v3 = config.crucible_3v3
    ctx.crucible_6v6 = config.crucible_6v6
    ctx.iron_banner = reset_ts in config.ib_week_resets
    ctx.trials_active = not ctx.iron_banner
    ctx.image_url = config.default_image_url

    # Best-effort Bungie derivations (never fatal — the team fills any gaps).
    async with aiohttp.ClientSession() as session:
        ctx.gm_strike = await derive_gm_nightfall(session)
    ctx.quickplay_weapon, ctx.control_weapon = await derive_playlist_weapons()
    ctx.zavala_options = await derive_zavala_options()

    return ctx


# ---------------------------------------------------------------------------
# Components V2 renderer
# ---------------------------------------------------------------------------


def _weekly_reward(name: str) -> str:
    return f"{name} - Weekly Reward" if name else "Weekly Reward"


def build_body(ctx: WeeklyResetContext) -> str:
    """The full post markdown, with ``:emoji:`` tokens still un-substituted."""
    lines: list[str] = ["# Weekly Reset Overview", "", f"Resets: <t:{ctx.reset_ts}:f>"]

    # EVENTS — only when there is something eventful to say.
    if ctx.iron_banner or ctx.events_narrative:
        lines += ["", "**EVENTS**", ""]
        if ctx.iron_banner:
            lines.append(f":IronBanner: {SEP} Iron Banner has returned!")
            lines += ["", TRIALS_IB_REMINDER]
        if ctx.events_narrative:
            lines += ["", ctx.events_narrative]

    # VANGUARD ALERTS
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
# Reconciliation (drop hand-picks the API no longer offers, on rebuild)
# ---------------------------------------------------------------------------


def reconcile_picks(ctx: WeeklyResetContext) -> list[str]:
    """Clear picks the refreshed API no longer offers; return the fields to flag."""
    flags: list[str] = []
    if ctx.zavala_weapon and ctx.zavala_options:
        hashes = {w.hash for w in ctx.zavala_options}
        if ctx.zavala_weapon.hash not in hashes:
            ctx.zavala_weapon = None
            flags.append("Zavala's Weapon")
    return flags


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
    channel_id = cfg.weekly_reset_drafts_channel
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

_SECTIONS: tuple[tuple[str, str], ...] = (
    ("vanguard", "Vanguard Alerts"),
    ("zavala", "Zavala's Weapon"),
    ("rotators", "Featured Raids & Dungeons"),
    ("pantheon", "Pantheon"),
    ("crucible", "Crucible Ops"),
    ("events", "Events & Trials"),
    ("notes", "Notes & Links"),
    ("image", "Image"),
)
_SECTION_LABELS = dict(_SECTIONS)

# A select "option" tuple: (label, value, is_default).
Option = tuple[str, str, bool]


def _select_a(ctx: WeeklyResetContext, section: str) -> tuple[str, list[Option]] | None:
    """Placeholder + options for the section's first select, if it has one."""
    if section == "zavala":
        if not ctx.zavala_options:
            return None
        current = ctx.zavala_weapon.hash if ctx.zavala_weapon else None
        opts = [
            (w.name[:100], str(w.hash), w.hash == current) for w in ctx.zavala_options
        ]
        return ("Pick Zavala's featured weapon", opts[:25])
    if section == "pantheon":
        pool = PANTHEON_BOSSES
        opts = [(b[:100], b, b == ctx.pantheon_reprise) for b in pool]
        return ("Pick the Reprise boss", opts[:25])
    if section == "events":
        opts = [
            ("Iron Banner: ON", "ib_on", ctx.iron_banner),
            ("Iron Banner: OFF", "ib_off", not ctx.iron_banner),
        ]
        return ("Iron Banner this week?", opts)
    return None


def _select_b(ctx: WeeklyResetContext, section: str) -> tuple[str, list[Option]] | None:
    if section == "pantheon":
        pool = PANTHEON_BOSSES
        opts = [(b[:100], b, b == ctx.pantheon_encore) for b in pool]
        return ("Pick the Encore boss", opts[:25])
    if section == "events":
        opts = [
            ("Trials line: ON", "trials_on", ctx.trials_active),
            ("Trials line: OFF", "trials_off", not ctx.trials_active),
        ]
        return ("Show the Trials line?", opts)
    return None


def _apply_select_a(ctx: WeeklyResetContext, section: str, value: str) -> None:
    if section == "zavala":
        pick = next((w for w in ctx.zavala_options if str(w.hash) == value), None)
        ctx.zavala_weapon = pick
    elif section == "pantheon":
        ctx.pantheon_reprise = value
    elif section == "events":
        ctx.iron_banner = value == "ib_on"
        if ctx.iron_banner:
            ctx.trials_active = False


def _apply_select_b(ctx: WeeklyResetContext, section: str, value: str) -> None:
    if section == "pantheon":
        ctx.pantheon_encore = value
    elif section == "events":
        ctx.trials_active = value == "trials_on"


# A modal field spec: (label, current_value, multiline).
Field = tuple[str, str, bool]


def _modal_spec(
    ctx: WeeklyResetContext, section: str
) -> tuple[str, list[Field]] | None:
    """Title + field specs for the section's free-text modal, if it has one."""
    if section == "vanguard":
        return (
            "Vanguard Alerts",
            [
                ("GM Nightfall strike", ctx.gm_strike, False),
                ("Quickplay weapon", _wname(ctx.quickplay_weapon), False),
                ("Control weapon", _wname(ctx.control_weapon), False),
                ("Seasonal featured raid", ctx.seasonal_raid, False),
                ("Seasonal featured dungeon", ctx.seasonal_dungeon, False),
            ],
        )
    if section == "zavala":
        return (
            "Zavala's Weapon (manual override)",
            [("Weapon name", _wname(ctx.zavala_weapon), False)],
        )
    if section == "rotators":
        return (
            "Featured Raids & Dungeons",
            [
                ("Raid 1", ctx.rotator_raids[0], False),
                ("Raid 2", ctx.rotator_raids[1], False),
                ("Dungeon 1", ctx.rotator_dungeons[0], False),
                ("Dungeon 2", ctx.rotator_dungeons[1], False),
            ],
        )
    if section == "crucible":
        return (
            "Crucible Ops",
            [
                ("1v6", ctx.crucible_1v6, False),
                ("3v3", ctx.crucible_3v3, False),
                ("6v6", ctx.crucible_6v6, False),
            ],
        )
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
    if section == "vanguard":
        gm, quick, control, s_raid, s_dungeon = (values + [""] * 5)[:5]
        ctx.gm_strike = gm.strip()
        ctx.quickplay_weapon = _merge_name(ctx.quickplay_weapon, quick)
        ctx.control_weapon = _merge_name(ctx.control_weapon, control)
        ctx.seasonal_raid = s_raid.strip()
        ctx.seasonal_dungeon = s_dungeon.strip()
    elif section == "zavala":
        ctx.zavala_weapon = _merge_name(ctx.zavala_weapon, values[0] if values else "")
    elif section == "rotators":
        r1, r2, d1, d2 = (values + [""] * 4)[:4]
        ctx.rotator_raids = (r1.strip(), r2.strip())
        ctx.rotator_dungeons = (d1.strip(), d2.strip())
    elif section == "crucible":
        c1, c3, c6 = (values + [""] * 3)[:3]
        ctx.crucible_1v6, ctx.crucible_3v3, ctx.crucible_6v6 = (
            c1.strip(),
            c3.strip(),
            c6.strip(),
        )
    elif section == "events":
        ctx.events_narrative = (values[0] if values else "").strip()
    elif section == "notes":
        notes_raw = values[0] if values else ""
        links_raw = values[1] if len(values) > 1 else ""
        ctx.notes = [line.strip() for line in notes_raw.splitlines() if line.strip()]
        ctx.extra_links = _parse_links(links_raw)
    elif section == "image":
        url = (values[0] if values else "").strip()
        ctx.image_url = url or None


def _wname(weapon: WeaponRef | None) -> str:
    return weapon.name if weapon else ""


def _merge_name(existing: WeaponRef | None, name: str) -> WeaponRef | None:
    """Keep the API hash/emoji when the typed name is unchanged; else plain text."""
    name = name.strip()
    if not name:
        return None
    if existing and existing.name == name:
        return existing
    return WeaponRef(name=name)


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
    section: str = "vanguard"
    confirm: bool = False


def _summary(ctx: WeeklyResetContext, section: str) -> str:
    """Compact view of the current section's values (the full preview is the card)."""
    if section == "vanguard":
        return (
            f"GM: {ctx.gm_strike or '—'}\n"
            f"Quickplay: {_wname(ctx.quickplay_weapon) or '—'}\n"
            f"Control: {_wname(ctx.control_weapon) or '—'}\n"
            f"Seasonal raid/dungeon: {ctx.seasonal_raid or '—'} / "
            f"{ctx.seasonal_dungeon or '—'}"
        )
    if section == "zavala":
        picked = _wname(ctx.zavala_weapon) or "— (pick one below)"
        return f"Zavala's Weapon: {picked}\nAPI options: {len(ctx.zavala_options)}"
    if section == "rotators":
        return (
            f"Raids: {' + '.join(x for x in ctx.rotator_raids if x) or '—'}\n"
            f"Dungeons: {' + '.join(x for x in ctx.rotator_dungeons if x) or '—'}"
        )
    if section == "pantheon":
        return (
            f"Reprise: {ctx.pantheon_reprise or '—'}\n"
            f"Encore: {ctx.pantheon_encore or '—'}"
        )
    if section == "crucible":
        return (
            f"1v6: {ctx.crucible_1v6 or '—'}\n"
            f"3v3: {ctx.crucible_3v3 or '—'}\n"
            f"6v6: {ctx.crucible_6v6 or '—'}"
        )
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
        container = build_container(
            [
                "## Publish weekly reset?",
                "This posts **exactly the card** in the drafts channel and crossposts "
                "it; beacon mirrors it to every follower.",
            ]
        )
        row = h.impl.MessageActionRowBuilder()
        row.add_interactive_button(
            h.ButtonStyle.SUCCESS, f"{sid}:confirm", label="Confirm publish"
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

    # Action buttons.
    buttons = h.impl.MessageActionRowBuilder()
    if _modal_spec(ctx, section):
        buttons.add_interactive_button(
            h.ButtonStyle.PRIMARY, f"{sid}:text", label="✏️ Edit text"
        )
    buttons.add_interactive_button(
        h.ButtonStyle.SUCCESS, f"{sid}:publish", label="Publish…"
    )
    buttons.add_interactive_button(
        h.ButtonStyle.DANGER, f"{sid}:discard", label="Discard draft"
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
            await utils.send_message(
                bot, hmessage, cfg.followables["weekly_reset"], crosspost=True
            )
            session.meta.status = "published"
            await save_meta(session.meta)
            _active_session = None
        await post_or_update_card(bot, session.ctx, session.meta)
        await mctx.respond(
            edit=True,
            flags=h.MessageFlag.IS_COMPONENTS_V2,
            components=[
                build_container(
                    ["✅ Published and crossposted — beacon will mirror it out."]
                )
            ],
        )
        mctx.stop_interacting()

    async def on_discard(mctx: lbc.MenuContext) -> None:
        global _active_session
        async with _draft_lock:
            if _active_session == session.session_id:
                _active_session = None
        await mctx.respond(
            edit=True,
            flags=h.MessageFlag.IS_COMPONENTS_V2,
            components=[build_container(["🗑️ Draft left as-is; editor closed."])],
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
        h.ButtonStyle.SUCCESS,
        on_publish,
        custom_id=f"{session.session_id}:publish",
        label="Publish",
    )
    menu.add_interactive_button(
        h.ButtonStyle.DANGER,
        on_discard,
        custom_id=f"{session.session_id}:discard",
        label="Discard",
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
    flags = reconcile_picks(ctx)

    async with _draft_lock:
        # A new reset supersedes any in-flight editor session for the old week.
        _active_session = None
        meta = DraftMeta(
            status="draft",
            needs_attention=flags,
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

# Activity field -> the manifest category its value autocompletes against. Suggestions
# are derived live from DestinyActivityDefinition (see _build_indexes); the option still
# accepts a free-typed value, so anything the manifest misses can still be typed.
_ACTIVITY_CATEGORY: dict[str, str] = {
    "gm_strike": "nightfall",
    "seasonal_raid": "raid",
    "rotator_raid_1": "raid",
    "rotator_raid_2": "raid",
    "seasonal_dungeon": "dungeon",
    "rotator_dungeon_1": "dungeon",
    "rotator_dungeon_2": "dungeon",
    "pantheon_reprise": "pantheon",
    "pantheon_encore": "pantheon",
    "crucible_1v6": "crucible",
    "crucible_3v3": "crucible",
    "crucible_6v6": "crucible",
}
# Reward field -> the DestinyItem itemType its value autocompletes over (3=weapon,
# 2=armour). Every reward slot today is a weapon, so only weapons are suggested; an
# armour slot would set 2 here and get only armour.
_REWARD_ITEM_TYPE: dict[str, int] = {
    "gm_weapon": 3,
    "quickplay_weapon": 3,
    "control_weapon": 3,
    "zavala_weapon": 3,
}
_ACTIVITY_FIELDS: tuple[tuple[str, str], ...] = (
    ("GM Nightfall strike", "gm_strike"),
    ("Seasonal featured raid", "seasonal_raid"),
    ("Seasonal featured dungeon", "seasonal_dungeon"),
    ("Featured raid 1", "rotator_raid_1"),
    ("Featured raid 2", "rotator_raid_2"),
    ("Featured dungeon 1", "rotator_dungeon_1"),
    ("Featured dungeon 2", "rotator_dungeon_2"),
    ("Pantheon Reprise", "pantheon_reprise"),
    ("Pantheon Encore", "pantheon_encore"),
    ("Crucible 1v6", "crucible_1v6"),
    ("Crucible 3v3", "crucible_3v3"),
    ("Crucible 6v6", "crucible_6v6"),
)
_REWARD_FIELDS: tuple[tuple[str, str], ...] = (
    ("GM Nightfall reward weapon", "gm_weapon"),
    ("Vanguard / Quickplay weapon", "quickplay_weapon"),
    ("Crucible / Control weapon", "control_weapon"),
    ("Zavala's Weapon", "zavala_weapon"),
)
_ACTIVITY_FIELD_CHOICES = [lb.Choice(label, key) for label, key in _ACTIVITY_FIELDS]
_REWARD_FIELD_CHOICES = [lb.Choice(label, key) for label, key in _REWARD_FIELDS]

# DestinyActivityModeType ids used to classify activities, + the PvP mode category.
_MODE_RAID = 4
_MODE_DUNGEON = 82
_MODE_NIGHTFALL = 46
_MODE_CATEGORY_PVP = 1
# Difficulty variants dropped so only base activity names are suggested.
_DIFFICULTY_TOKENS = (
    "master",
    "legend",
    "grandmaster",
    "expert",
    "adept",
    "normal",
    "contest",
)
_NIGHTFALL_PREFIXES = ("Nightfall Grandmaster: ", "Grandmaster: ", "Nightfall: ")


@dataclasses.dataclass
class _Indexes:
    """Manifest-derived autocomplete data, built once and cached."""

    #: (name, hash, itemTypeDisplayName, itemType) for every weapon/armour.
    items: list[tuple[str, int, str, int]]
    #: category ("raid"/"dungeon"/"nightfall"/"pantheon"/"crucible") -> sorted names.
    activities: dict[str, list[str]]


_indexes: _Indexes | None = None
_indexes_lock = asyncio.Lock()


def _classify_activity(defn: dict[str, t.Any], type_name: str = "") -> str | None:
    """Classify a DestinyActivityDefinition, or None if it's none of our categories.

    ``type_name`` is the activity's resolved DestinyActivityTypeDefinition name — the
    authoritative signal. We fall back to the activity mode, then (only when there is no
    type *and* no mode at all) to fireteam size, so typed activities like strikes and
    story missions can't flood the raid/dungeon lists.
    """
    type_lower = type_name.lower()
    if type_lower == "raid":
        return "raid"
    if type_lower == "dungeon":
        return "dungeon"
    if "nightfall" in type_lower:
        return "nightfall"

    modes = set(defn.get("activityModeTypes") or [])
    direct = defn.get("directActivityModeType")
    if direct:
        modes.add(direct)
    if _MODE_RAID in modes:
        return "raid"
    if _MODE_DUNGEON in modes:
        return "dungeon"
    if _MODE_NIGHTFALL in modes:
        return "nightfall"

    name = (defn.get("displayProperties") or {}).get("name", "")
    if "pantheon" in name.lower():
        return "pantheon"

    # Last resort — only when the manifest gives neither a type nor a mode: fireteam
    # size (raids cap at 6, dungeons at 3).
    if not type_name and not modes:
        max_party = (defn.get("matchmaking") or {}).get("maxParty")
        if max_party == 6:
            return "raid"
        if max_party == 3:
            return "dungeon"
    return None


def _clean_activity_name(name: str, category: str) -> str:
    """Normalise to the base activity name; "" to drop a difficulty variant."""
    name = name.strip()
    if not name:
        return ""
    if category == "nightfall":
        for prefix in _NIGHTFALL_PREFIXES:
            if name.startswith(prefix):
                name = name[len(prefix) :]
                break
        return name.strip()
    if category == "pantheon":
        return name.removeprefix("Pantheon: ").strip()
    lowered = name.lower()
    if any(token in lowered for token in _DIFFICULTY_TOKENS):
        return ""  # a difficulty variant; the base name is kept from its own row
    return name


async def _build_indexes() -> _Indexes:
    items: list[tuple[str, int, str, int]] = []
    activities: dict[str, set[str]] = {
        "raid": set(),
        "dungeon": set(),
        "nightfall": set(),
        "pantheon": set(),
        "crucible": set(),
    }
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
                name = (defn.get("displayProperties") or {}).get("name")
                if not name:
                    continue
                items.append(
                    (
                        name,
                        int(defn["hash"]),
                        defn.get("itemTypeDisplayName", ""),
                        item_type,
                    )
                )

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
                category = _classify_activity(defn, type_name)
                if category is None:
                    continue
                name = _clean_activity_name(
                    (defn.get("displayProperties") or {}).get("name", ""), category
                )
                if name:
                    activities[category].add(name)

            await cur.execute("SELECT json FROM DestinyActivityModeDefinition")
            for (row,) in await cur.fetchall():
                defn = json.loads(row)
                if defn.get("activityModeCategory") != _MODE_CATEGORY_PVP:
                    continue
                name = (defn.get("displayProperties") or {}).get("name")
                if name:
                    activities["crucible"].add(name)
    except Exception:
        logger.warning("weekly_reset: manifest index build failed", exc_info=True)

    result = _Indexes(
        items=items,
        activities={key: sorted(names) for key, names in activities.items()},
    )
    logger.info(
        "weekly_reset indexes: %d items; raids=%d dungeons=%d nightfalls=%d "
        "pantheon=%d crucible=%d",
        len(result.items),
        len(result.activities["raid"]),
        len(result.activities["dungeon"]),
        len(result.activities["nightfall"]),
        len(result.activities["pantheon"]),
        len(result.activities["crucible"]),
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


async def _activity_value_autocomplete(ctx: "lb.AutocompleteContext[str]") -> None:
    field_opt = ctx.get_option("field")
    field = str(field_opt.value) if field_opt and field_opt.value else ""
    category = _ACTIVITY_CATEGORY.get(field, "")
    if _indexes is None:
        asyncio.create_task(get_indexes())  # warm for the next keystroke
        await ctx.respond([])
        return
    names = _indexes.activities.get(category, [])
    query = str(ctx.focused.value or "").lower()
    matches = [name for name in names if query in name.lower()] if query else names
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
    for name, hash_, type_name, item_type in _indexes.items:
        if item_type != allowed or query not in name.lower():
            continue
        label = f"{name} — {type_name}" if type_name else name
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
        for name, hash_, type_name, _item_type in indexes.items:
            if hash_ == wanted:
                return WeaponRef(name, hash_, api.likely_emoji_name(type_name))
    for name, hash_, type_name, _item_type in indexes.items:
        if name.lower() == value.lower():
            return WeaponRef(name, hash_, api.likely_emoji_name(type_name))
    return WeaponRef(name=value)


def apply_activity_field(ctx: WeeklyResetContext, field: str, value: str) -> None:
    if field == "rotator_raid_1":
        ctx.rotator_raids = (value, ctx.rotator_raids[1])
    elif field == "rotator_raid_2":
        ctx.rotator_raids = (ctx.rotator_raids[0], value)
    elif field == "rotator_dungeon_1":
        ctx.rotator_dungeons = (value, ctx.rotator_dungeons[1])
    elif field == "rotator_dungeon_2":
        ctx.rotator_dungeons = (ctx.rotator_dungeons[0], value)
    elif field in _ACTIVITY_CATEGORY:
        setattr(ctx, field, value)


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
class WeeklyResetSetActivity(
    lb.SlashCommand,
    name="set_activity",
    description="Set an activity field (autocompletes names for that category)",
):
    field = lb.string("field", "Which activity slot", choices=_ACTIVITY_FIELD_CHOICES)
    value = lb.string(
        "value",
        "Start typing — suggestions are for the chosen category",
        autocomplete=_activity_value_autocomplete,
    )

    @lb.invoke
    async def invoke(
        self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED
    ) -> None:
        field, value = self.field, self.value.strip()
        await mutate_draft(
            bot, ctx.user.id, lambda c: apply_activity_field(c, field, value)
        )
        await ctx.respond(f"Set **{field}** → {value or '—'}", ephemeral=True)


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
    if not cfg.weekly_reset_drafts_channel:
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


if cfg.weekly_reset_drafts_channel:
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
        "Weekly-reset autopost dormant: set WEEKLY_RESET_DRAFTS_CHANNEL_ID to enable "
        "the /weekly_reset commands + reset-day draft."
    )
