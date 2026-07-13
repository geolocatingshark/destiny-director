"""Shared loading + Components-V2 rendering of the legacy world-activity posts.

Mirrors :mod:`dd.common.lost_sector` (loader + ``format_post``) but for the
per-destination legacy rotations. There are no autoposts, so this module is consumed
only by the beacon read commands and the anchor web-editor preview — there is no
scheduler, follow/mirror, or Google-Sheet fallback.

Rendering produces a list of markdown *sections* (each a text display, divider-separated
in the CV2 container — see :func:`dd.common.components.build_container`), styled after
``/distortion``: ``##`` headers, a ``###`` current-highlight, ``**Upcoming**`` lists and
``-#`` subtext. Dates are shown as ``Mmm DD`` (no year) and loot is tagged with
weapon-type / ``:armor:`` emoji via the shared emoji substituter.
"""

import datetime as dt
import logging

from ..sector_accounting.legacy_activities import (
    LegacyRotation,
    ResolvedActivity,
)
from . import rotation_schema, schemas
from .utils import construct_emoji_substituter, re_user_side_emoji

# Last-known-good rotation per slug: served only if the DB read/parse fails, so a
# transient DB blip doesn't break a command mid-session.
_rotation_cache: dict[str, LegacyRotation] = {}


async def load_rotation(destination_key: str) -> LegacyRotation:
    """Load a destination's rotation from the DB JSON store (``legacy_<key>``).

    DB every call (cheap; editor saves take effect immediately) → last-known-good cache
    on total failure. No Google-Sheet fallback (these types are editor/seed authored).
    """
    slug = f"legacy_{destination_key}"

    try:
        doc = await schemas.RotationData.get_data(slug)
    except Exception:
        logging.exception("Failed to read %s rotation from the DB", slug)
        doc = None

    if doc is not None:
        try:
            rotation = LegacyRotation.from_json(doc)
            _rotation_cache[slug] = rotation
            return rotation
        except Exception:
            logging.exception("Stored %s rotation JSON is malformed", slug)

    cached = _rotation_cache.get(slug)
    if cached is not None:
        return cached
    raise RuntimeError(f"No rotation data available for {slug}")


# --- formatting helpers -----------------------------------------------------------

_FOOTER = (
    "-# [Kyber's Corner](https://kyberscorner.com/destiny2/legacy-activities/)"
    " · [Support](https://ko-fi.com/Kyber3000)"
)


def _field_label(name: str) -> str:
    return name.replace("_", " ").title()


def _fmt(date: dt.datetime) -> str:
    """A compact, year-less date, e.g. ``Jul 14``."""
    return f"{date:%b} {date.day}"


# Weapon-type emoji that exist in the Kyber server (same set matched in
# ``dd.anchor.extensions.portal_ops``). Longest names first so e.g. "linear fusion
# rifle" matches before "fusion rifle".
_WEAPON_TYPE_SLUGS = (
    "auto_rifle",
    "hand_cannon",
    "pulse_rifle",
    "scout_rifle",
    "sidearm",
    "submachine_gun",
    "shotgun",
    "sniper_rifle",
    "fusion_rifle",
    "linear_fusion_rifle",
    "grenade_launcher",
    "rocket_launcher",
    "machine_gun",
    "sword",
    "glaive",
    "combat_bow",
    "trace_rifle",
)
_WEAPON_TYPES = sorted(
    ((slug.replace("_", " "), slug) for slug in _WEAPON_TYPE_SLUGS),
    key=lambda pair: -len(pair[0]),
)


def _weapon_emoji(text: str) -> str | None:
    """The ``:weapon_type:`` emoji token for a weapon string, or ``None`` if it names
    no known weapon type (so non-weapons — bosses, locations — stay un-prefixed)."""
    low = text.lower()
    for name, slug in _WEAPON_TYPES:
        if name in low:
            return f":{slug}:"
    if "bow" in low:
        return ":combat_bow:"
    return None


def _weaponize(text: str) -> str:
    """Prefix a weapon with its type emoji and drop the ``(Type)`` suffix; a non-weapon
    is returned unchanged."""
    emoji = _weapon_emoji(text)
    if emoji is None:
        return text
    return f"{emoji} {text.split(' (')[0].strip()}"


def _dares_weapon(text: str) -> str:
    """Like :func:`_weaponize` but always shows an emoji (``:weapon:`` fallback)."""
    emoji = _weapon_emoji(text) or ":weapon:"
    return f"{emoji} {text.split(' (')[0].strip()}"


def _armorize(text: str) -> str:
    return f":armor: {text}"


def _sub(text: str, emoji_dict: dict) -> str:
    return re_user_side_emoji.sub(construct_emoji_substituter(emoji_dict), text)


def _subbed(sections: list[str], emoji_dict: dict) -> list[str]:
    return [_sub(s, emoji_dict) for s in sections]


def reset_week_start(rotation: LegacyRotation, when: dt.datetime) -> dt.datetime:
    """The weekly-reset boundary (Tuesday 17:00 UTC) on/before ``when``."""
    weeks = (when - rotation.start_date).days // 7
    return rotation.start_date + dt.timedelta(days=7 * weeks)


def period_starts(
    rotation: LegacyRotation, when: dt.datetime, count: int
) -> list[dt.datetime]:
    """``count`` reset boundaries starting with the one active at ``when`` (aligned to
    the rotation's day/week cadence)."""
    step = rotation.step
    n = (when - rotation.start_date) // step
    first = rotation.start_date + step * n
    return [first + step * i for i in range(count)]


def _inline_values(activity: ResolvedActivity) -> str:
    """A compact one-line summary of an activity (weapon-tagged) for day/week rows."""
    if activity.set is not None:
        return activity.set.name or "TBC"
    values = [_weaponize(v) for v in activity.values.values() if v]
    return " · ".join(values) if values else "TBC"


def _activity_block(activity: ResolvedActivity) -> str:
    """A ``**Title**`` block with the activity's current value(s), weapon-tagged."""
    if activity.is_empty:
        return f"**{activity.title}**\n*TBC*"
    if len(activity.values) == 1:
        value = _weaponize(next(iter(activity.values.values())))
        return f"**{activity.title}**\n{value}"
    lines = [
        f"{_field_label(name)}: {_weaponize(value)}"
        for name, value in activity.values.items()
        if value
    ]
    return f"**{activity.title}**\n" + "\n".join(lines)


def _set_sections(activity: ResolvedActivity) -> list[str]:
    """The Dares loot set as its own sections: header, weapons, armor."""
    live = activity.set
    if live is None:
        return [f"**{activity.title}**\n*TBC*"]
    sections = [f"### 🎲 {live.name}"]
    if live.weapons:
        sections.append(
            "**Weapons**\n" + "\n".join(_dares_weapon(w) for w in live.weapons)
        )
    if live.armor:
        # Armor is identical across the three classes, so name it once.
        armor = "\n".join(_armorize(a) for a in live.armor)
        sections.append(f"**Armor**\n{armor}\n-# available for all classes")
    return sections


# --- renderers (each returns a list of markdown sections) -------------------------


def render_date_sections(
    destination_key: str,
    resolved: list[ResolvedActivity],
    date: dt.datetime,
    *,
    emoji_dict: dict,
) -> list[str]:
    """A single date's page (navigator mode): the day's/week's activities."""
    title = rotation_schema.LEGACY_DESTINATIONS[destination_key][0]
    blocks: list[str] = []
    set_sections: list[str] = []
    for activity in resolved:
        if activity.set is not None:
            set_sections = _set_sections(activity)
        else:
            blocks.append(_activity_block(activity))

    sections = [f"# {title}\n-# {_fmt(date)}"]
    if blocks:
        sections.append("\n\n".join(blocks))
    sections += set_sections
    sections.append(_FOOTER)
    return _subbed(sections, emoji_dict)


def render_week_sections(
    destination_key: str,
    rotation: LegacyRotation,
    week_start: dt.datetime,
    *,
    emoji_dict: dict,
) -> list[str]:
    """One reset-week: weekly activities once, plus a per-day breakdown of the daily
    activities across the seven days of that week."""
    title = rotation_schema.LEGACY_DESTINATIONS[destination_key][0]
    days = [week_start + dt.timedelta(days=i) for i in range(7)]
    per_day = [rotation(d) for d in days]
    week_activities = per_day[0]

    sections = [f"# {title}\n-# Week of {_fmt(week_start)}"]

    weekly = [a for a in week_activities if a.cadence == "weekly"]
    if weekly:
        sections.append("\n\n".join(_activity_block(a) for a in weekly))

    for pos, activity in enumerate(week_activities):
        if activity.cadence != "daily":
            continue
        rows = "\n".join(
            f"{_fmt(days[i])} · {_inline_values(per_day[i][pos])}" for i in range(7)
        )
        sections.append(f"**{activity.title}** · daily\n\n{rows}")

    sections.append(_FOOTER)
    return _subbed(sections, emoji_dict)


def _row_value(activity: ResolvedActivity, *, armor: bool) -> str:
    if activity.set is not None:
        return activity.set.name or "TBC"
    values = [
        (_armorize(v) if armor else _weaponize(v))
        for v in activity.values.values()
        if v
    ]
    return " · ".join(values) if values else "TBC"


def render_upcoming_sections(
    destination_key: str,
    rotation: LegacyRotation,
    dates: list[dt.datetime],
    *,
    emoji_dict: dict,
    armor: bool = False,
) -> list[str]:
    """A single, non-paginated "A Look Ahead" post (after Kyber's schedule embeds).

    A title, a live resets-countdown, then ``**date**`` / ``▸ value`` rows with the
    current one marked. ``dates`` are reset-aligned: ``dates[0]`` is the current period
    and ``dates[1]`` the next boundary (the countdown target)."""
    weekly = rotation.step.days == 7
    per_date = [(d, rotation(d)) for d in dates]
    step = rotation.step

    lines: list[str] = []
    for pos, first in enumerate(per_date[0][1]):
        lines += [
            f"# {first.title}",
            "*`A Look Ahead`*",
            f"-# Resets <t:{int(dates[1].timestamp())}:R>",
            "",
        ]
        if first.set is None and len(first.values) > 1:
            lines.append("-# " + " · ".join(_field_label(n) for n in first.values))
            lines.append("")
        for i, (day, acts) in enumerate(per_date):
            span = f"{_fmt(day)} - {_fmt(day + step)}" if weekly else _fmt(day)
            marker = "  ·  *now*" if i == 0 else ""
            lines.append(f"**{span}**{marker}")
            lines.append(f"▸ {_row_value(acts[pos], armor=armor)}")
            lines.append("")

    lines.append(
        "-# *(sequence repeats)* · "
        "[Kyber's Corner](https://kyberscorner.com/destiny2/legacy-activities/)"
    )
    return _subbed(["\n".join(lines)], emoji_dict)


# Fancy title matching Kyber's Dares embed ("𝑜𝑓" is math-italic o + f).
_DARES_TITLE = "# Dares 𝑜𝑓 Eternity"


def render_dares_sections(
    resolved: list[ResolvedActivity],
    date: dt.datetime,
    *,
    emoji_dict: dict,
) -> list[str]:
    """The Dares of Eternity page, after Kyber's embed: expert rounds (an arrow chain),
    then Legendary Armor and Legendary Weapons for the week's set."""
    rounds = next((a for a in resolved if a.key == "rounds"), None)
    loot = next((a for a in resolved if a.set is not None), None)

    sections = [f"{_DARES_TITLE}\n-# Week of {_fmt(date)}"]

    if rounds is not None and not rounds.is_empty:
        chain = "⇢ ".join(v for v in rounds.values.values() if v)
        sections.append(f"**Expert Rounds**\n\n:30th_annv: {chain}")

    if loot is not None and loot.set is not None:
        live = loot.set
        if live.armor:
            armor = "\n".join(f":armor: {a}" for a in live.armor)
            sections.append(
                f"**Legendary Armor // {live.name}**\n\n{armor}"
                "\n-# available for all classes"
            )
        if live.weapons:
            weapons = "\n".join(_dares_weapon(w) for w in live.weapons)
            sections.append(f"**Legendary Weapons // {live.name}**\n\n{weapons}")

    sections.append(
        "**Other**\n\n"
        "[View more details](https://kyberscorner.com/destiny2/legacy-activities/) ↗"
    )
    return _subbed(sections, emoji_dict)
