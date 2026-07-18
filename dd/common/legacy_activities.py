"""Shared loading + Components-V2 rendering of the legacy world-activity posts.

Mirrors :mod:`dd.common.lost_sector` (loader + ``format_post``) but for the
per-destination legacy rotations. There are no autoposts, so this module is consumed
only by the beacon read commands and the anchor web-editor preview — there is no
scheduler, follow/mirror, or Google-Sheet fallback.

Rendering produces a list of markdown *sections* (each a text display, divider-separated
in the CV2 container — see :func:`dd.common.components.build_container`), styled after
Kyber's embeds: a ``#`` title, ``**bold**`` section labels and ``-#`` subtext. Dates are
shown as ``Mmm DD`` (no year); loot is tagged with weapon-type / ``:armor:`` emoji and,
when a light.gg URL has been baked into the doc (``item_links``), weapons are linked.
"""

import datetime as dt
import json
import logging
import pathlib

from ..sector_accounting.legacy_activities import (
    LegacyRotation,
    ResolvedActivity,
)
from . import rotation_schema, schemas
from .utils import construct_emoji_substituter, re_user_side_emoji

# Committed seed documents (one per destination key), shipped with both bots. They carry
# their own baked ``item_links`` so an auto-seed needs no manifest access — see
# ``_autoseed`` and ``dd/anchor/seed_legacy_rotations.py --bake-files``.
_SEED_DIR = pathlib.Path(__file__).resolve().parent / "seed_data" / "world_activity"

# Last-known-good rotation per slug: served only if the DB read/parse fails, so a
# transient DB blip doesn't break a command mid-session.
_rotation_cache: dict[str, LegacyRotation] = {}

# Render-mode partitions, shared by the beacon read commands + the anchor preview wall:
# - single: short cycles → one non-paginated current+upcoming post.
# - week-daily: a weekly navigator with a per-day breakdown of the daily activities.
# - navigator (everything else): one page per day (daily) or per week (weekly).
SINGLE_DESTINATIONS = frozenset({"rahool", "pale_heart", "kepler"})
WEEK_DAILY_DESTINATIONS = frozenset({"neomuna", "moon"})


def load_seed_doc(destination_key: str) -> dict | None:
    """The committed seed document for a destination key, or ``None`` if absent.

    These docs are the "known-good defaults": they carry their own baked ``item_links``
    so they render (and reset) with no manifest access. Used both by :func:`_autoseed`
    (first-use seeding) and the web editor's *Reset to defaults* action."""
    try:
        raw = (_SEED_DIR / f"{destination_key}.json").read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return json.loads(raw)


async def _autoseed(slug: str, destination_key: str) -> dict | None:
    """Populate an absent destination row from its committed seed doc, and return it.

    Each command self-seeds on first use so a freshly-deployed bot (or a wiped row)
    serves data with no manual seed step. Persisting is best-effort — the doc is
    returned for rendering even if the write fails."""
    doc = load_seed_doc(destination_key)
    if doc is None:
        return None
    try:
        await schemas.RotationData.set_data(slug, doc)
        logging.info("Auto-seeded %s from committed seed data", slug)
    except Exception:
        logging.exception(
            "Failed to persist auto-seed for %s (serving in-memory)", slug
        )
    return doc


async def load_rotation(destination_key: str) -> LegacyRotation:
    """Load a destination's rotation from the DB JSON store (``world_activity_<key>``).

    DB every call (cheap; editor saves take effect immediately). An *absent* row is
    auto-seeded from the committed seed doc (per-command, independent). A read/parse
    failure falls back to the last-known-good cache. No Google-Sheet fallback (these
    types are editor/seed authored).
    """
    slug = rotation_schema.rotation_slug(destination_key)

    db_ok = True
    try:
        doc = await schemas.RotationData.get_data(slug)
    except Exception:
        logging.exception("Failed to read %s rotation from the DB", slug)
        doc, db_ok = None, False

    # Only auto-seed on a *clean* absent read — a transient DB error must not overwrite
    # a row that may exist, so it falls through to the cache instead.
    if doc is None and db_ok:
        doc = await _autoseed(slug, destination_key)

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


def _weapon_type_hint(value: str) -> str | None:
    """The lower-cased ``(Type)`` hint from a stored ``Name (Type)`` weapon value.

    ``None`` when the value carries no parenthetical (a bare boss/mode/location name).
    Handles the nested Dares shape too: ``Sojourner's Tale (Shotgun (Solar))`` → the
    hint still starts with ``shotgun``."""
    name = value.split(" (")[0]
    if len(name) >= len(value):
        return None
    return value[len(name) :].strip(" ()").lower()


def _weapon_emoji(text: str) -> str | None:
    """The ``:weapon_type:`` emoji token for a weapon value (``Name (Type)``), or
    ``None`` if it names no known weapon type (so non-weapons — bosses, locations —
    stay un-prefixed).

    Matches only the ``(Type)`` hint, never the free-form name. The previous approach
    scanned the whole value as a substring, which mislabels any non-weapon value that
    merely *contains* a weapon word — e.g. ``Swordbearer`` → ``sword``, or a value with
    ``bow`` in it → ``combat_bow`` — and then :func:`_linked` would strip its trailing
    ``(…)`` qualifier. This mirrors ``portal_ops._reward_emoji``, which sidesteps the
    same trap by matching Bungie's structured ``itemTypeDisplayName`` (the type field)
    rather than the display name; here the value has no separate type field, so we
    recover it from the parenthetical."""
    hint = _weapon_type_hint(text)
    if hint is None:
        return None
    for name, slug in _WEAPON_TYPES:
        if name in hint:
            return f":{slug}:"
    if "bow" in hint:
        return ":combat_bow:"
    return None


def _linked(text: str, links: dict[str, str]) -> str:
    """The bare item name (``(Type)`` dropped), hyperlinked to light.gg if a URL was
    baked for this value."""
    name = text.split(" (")[0].strip()
    url = links.get(text)
    return f"[{name}]({url})" if url else name


def _weaponize(text: str, links: dict[str, str]) -> str:
    """Prefix a weapon with its type emoji and link it; a non-weapon is unchanged."""
    emoji = _weapon_emoji(text)
    if emoji is None:
        return text
    return f"{emoji} {_linked(text, links)}"


def _dares_weapon(text: str, links: dict[str, str]) -> str:
    """Like :func:`_weaponize` but always shows an emoji (``:weapon:`` fallback)."""
    emoji = _weapon_emoji(text) or ":weapon:"
    return f"{emoji} {_linked(text, links)}"


def _armorize(text: str) -> str:
    return f":armor: {text}"


def is_weapon_value(value: str) -> bool:
    """Whether a stored value looks like a weapon (names a known weapon type)."""
    return _weapon_emoji(value) is not None


def weapon_slot_values(doc: dict) -> set[str]:
    """Every distinct value sitting in a *weapon slot* — a set's ``weapons`` list or an
    element whose name mentions "weapon" — regardless of whether it parses as a weapon.

    Unlike :func:`weapon_values` this does **not** filter on :func:`is_weapon_value`, so
    it still surfaces a value whose ``(Type)`` is misspelled (e.g. ``Auto Rilfe``). The
    seed's link step uses it to warn about weapons that would otherwise be dropped
    silently and never get a light.gg link."""
    found: set[str] = set()
    for activity in doc.get("activities", []):
        if activity.get("kind") == "sets":
            for gear_set in activity.get("sets", []):
                found.update(gear_set.get("weapons", []))
        else:
            for element in activity.get("elements", []):
                if "weapon" in element.get("name", "").lower():
                    found.update(element.get("values", []))
    return found


def weapon_values(doc: dict) -> set[str]:
    """Every distinct weapon-looking value in a legacy doc (for light.gg links)."""
    found: set[str] = set()
    for activity in doc.get("activities", []):
        if activity.get("kind") == "sets":
            for gear_set in activity.get("sets", []):
                found.update(
                    w for w in gear_set.get("weapons", []) if is_weapon_value(w)
                )
        else:
            for element in activity.get("elements", []):
                found.update(v for v in element.get("values", []) if is_weapon_value(v))
    return found


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


def _inline_values(activity: ResolvedActivity, links: dict[str, str]) -> str:
    """A compact one-line summary of an activity (weapon-tagged) for day/week rows."""
    if activity.set is not None:
        return activity.set.name or "TBC"
    values = [_weaponize(v, links) for v in activity.values.values() if v]
    return " · ".join(values) if values else "TBC"


def _activity_block(activity: ResolvedActivity, links: dict[str, str]) -> str:
    """A ``**Title**`` block with the activity's current value(s), weapon-tagged."""
    if activity.is_empty:
        return f"**{activity.title}**\n*TBC*"
    if len(activity.values) == 1:
        value = _weaponize(next(iter(activity.values.values())), links)
        return f"**{activity.title}**\n{value}"
    lines = [
        f"{_field_label(name)}: {_weaponize(value, links)}"
        for name, value in activity.values.items()
        if value
    ]
    return f"**{activity.title}**\n" + "\n".join(lines)


def _set_sections(activity: ResolvedActivity, links: dict[str, str]) -> list[str]:
    """The Dares loot set as its own sections: header, weapons, armor."""
    live = activity.set
    if live is None:
        return [f"**{activity.title}**\n*TBC*"]
    sections = [f"### 🎲 {live.name}"]
    if live.weapons:
        sections.append(
            "**Weapons**\n" + "\n".join(_dares_weapon(w, links) for w in live.weapons)
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
    links: dict[str, str] | None = None,
) -> list[str]:
    """A single date's page (navigator mode): the day's/week's activities."""
    links = links or {}
    title = rotation_schema.LEGACY_DESTINATIONS[destination_key][0]
    blocks: list[str] = []
    set_sections: list[str] = []
    for activity in resolved:
        if activity.set is not None:
            set_sections = _set_sections(activity, links)
        else:
            blocks.append(_activity_block(activity, links))

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
    links: dict[str, str] | None = None,
) -> list[str]:
    """One reset-week: weekly activities once, plus a per-day breakdown of the daily
    activities across the seven days of that week."""
    links = links or {}
    title = rotation_schema.LEGACY_DESTINATIONS[destination_key][0]
    days = [week_start + dt.timedelta(days=i) for i in range(7)]
    per_day = [rotation(d) for d in days]
    week_activities = per_day[0]

    sections = [f"# {title}\n-# Week of {_fmt(week_start)}"]

    weekly = [a for a in week_activities if a.cadence == "weekly"]
    if weekly:
        sections.append("\n\n".join(_activity_block(a, links) for a in weekly))

    for pos, activity in enumerate(week_activities):
        if activity.cadence != "daily":
            continue
        rows = "\n".join(
            f"{_fmt(days[i])} · {_inline_values(per_day[i][pos], links)}"
            for i in range(7)
        )
        sections.append(f"**{activity.title}** · daily\n\n{rows}")

    sections.append(_FOOTER)
    return _subbed(sections, emoji_dict)


def render_upcoming_sections(
    destination_key: str,
    rotation: LegacyRotation,
    dates: list[dt.datetime],
    *,
    emoji_dict: dict,
    links: dict[str, str] | None = None,
) -> list[str]:
    """A single, non-paginated post styled after ``/distortion``: a title, the current
    focus highlighted in its own divider-flanked section with a live reset countdown,
    then an upcoming list of ``value — date`` rows.

    ``dates`` are reset-aligned: ``dates[0]`` is the current period and ``dates[1]`` the
    next boundary (the countdown target)."""
    links = links or {}
    weekly = rotation.step.days == 7
    per_date = [(d, rotation(d)) for d in dates]
    step = rotation.step

    def when(day: dt.datetime) -> str:
        return f"{_fmt(day)} - {_fmt(day + step)}" if weekly else _fmt(day)

    sections: list[str] = []
    for pos, first in enumerate(per_date[0][1]):
        # Title (with the field labels as subtext for a multi-element activity).
        title = f"# {first.title}"
        if first.set is None and len(first.values) > 1:
            title += "\n-# " + " · ".join(_field_label(n) for n in first.values)
        sections.append(title)

        # Current focus — its own section (divider-flanked) with a live reset countdown.
        current = _inline_values(per_date[0][1][pos], links)
        sections.append(
            f"### {current}\n-# Now · resets <t:{int(dates[1].timestamp())}:R>"
        )

        # Upcoming: the value first, then the date it applies.
        rows = "\n".join(
            f"{_inline_values(acts[pos], links)} — {when(day)}"
            for day, acts in per_date[1:]
        )
        sections.append(f"**Upcoming**\n{rows}")

    sections.append(_FOOTER)
    return _subbed(sections, emoji_dict)


# Fancy title matching Kyber's Dares embed ("𝑜𝑓" is math-italic o + f).
_DARES_TITLE = "# Dares 𝑜𝑓 Eternity"


def render_dares_sections(
    resolved: list[ResolvedActivity],
    date: dt.datetime,
    *,
    emoji_dict: dict,
    links: dict[str, str] | None = None,
) -> list[str]:
    """The Dares of Eternity page, after Kyber's embed: expert rounds (an arrow chain),
    then Legendary Armor and Legendary Weapons for the week's set."""
    links = links or {}
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
            weapons = "\n".join(_dares_weapon(w, links) for w in live.weapons)
            sections.append(f"**Legendary Weapons // {live.name}**\n\n{weapons}")

    sections.append(_FOOTER)
    return _subbed(sections, emoji_dict)


# --- preview wall -----------------------------------------------------------------

# Forward-window sizes for the anchor preview wall (independent of the beacon
# navigator's own page counts).
_WALL_DAILY_COUNT = 14  # navigator, daily destination (two weeks)
_WALL_WEEKLY_COUNT = 8  # navigator / week-daily destination (two months)
_WALL_SINGLE_DAILY_ROWS = 7  # single mode, daily destination
_WALL_SINGLE_WEEKLY_ROWS = 5  # single mode, weekly destination


def iter_wall_posts(
    destination_key: str,
    rotation: LegacyRotation,
    now: dt.datetime,
    count: int | None = None,
) -> list[tuple[str, str]]:
    """Preview-wall entries for a legacy destination: ``(period label, body markdown)``.

    Mirrors the beacon read command's per-mode paging (single / week-daily / navigator)
    but returns raw-``:emoji:`` markdown bodies (each post's sections joined with a
    blank line) for :func:`dd.anchor.hybrid_post_core.render_post_spec`. The renderers
    are called with an EMPTY ``emoji_dict`` on purpose so ``:name:`` tokens survive for
    the HTML previewer to turn into ``<img>`` (``construct_emoji_substituter`` leaves
    names it doesn't know untouched). Single-mode destinations yield ONE entry (current
    + upcoming); navigator / week-daily yield the forward window of per-period posts,
    capped at ``count`` when given (e.g. the rotation editor's compact preview).
    """
    links = rotation.item_links
    weekly = rotation.step.days == 7

    if destination_key in SINGLE_DESTINATIONS:
        rows = _WALL_SINGLE_WEEKLY_ROWS if weekly else _WALL_SINGLE_DAILY_ROWS
        # +1: the aligned window's first entry is the current period.
        dates = period_starts(rotation, now, rows + 1)
        sections = render_upcoming_sections(
            destination_key, rotation, dates, emoji_dict={}, links=links
        )
        return [("Current + upcoming", "\n\n".join(sections))]

    if destination_key in WEEK_DAILY_DESTINATIONS:
        week0 = reset_week_start(rotation, now)
        week_posts: list[tuple[str, str]] = []
        for offset in range(count or _WALL_WEEKLY_COUNT):
            week_start = week0 + dt.timedelta(days=7 * offset)
            sections = render_week_sections(
                destination_key, rotation, week_start, emoji_dict={}, links=links
            )
            week_posts.append((f"Week of {_fmt(week_start)}", "\n\n".join(sections)))
        return week_posts

    # navigator (daily or weekly): one post per reset-aligned period.
    n = count or (_WALL_WEEKLY_COUNT if weekly else _WALL_DAILY_COUNT)
    posts: list[tuple[str, str]] = []
    for date in period_starts(rotation, now, n):
        if destination_key == "dares":
            sections = render_dares_sections(
                rotation(date), date, emoji_dict={}, links=links
            )
        else:
            sections = render_date_sections(
                destination_key, rotation(date), date, emoji_dict={}, links=links
            )
        label = f"Week of {_fmt(date)}" if weekly else _fmt(date)
        posts.append((label, "\n\n".join(sections)))
    return posts
