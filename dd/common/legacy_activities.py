"""Shared loading + Components-V2 rendering of the legacy world-activity posts.

Mirrors :mod:`dd.common.lost_sector` (loader + ``format_post``) but for the
per-destination legacy rotations. There are no autoposts, so this module is consumed
only by the beacon read commands and the anchor web-editor preview — there is no
scheduler, follow/mirror, or Google-Sheet fallback.
"""

import datetime as dt
import logging

import hikari as h

from ..sector_accounting.legacy_activities import (
    LegacyRotation,
    ResolvedActivity,
    ResolvedSet,
)
from . import cfg, components, rotation_schema, schemas
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


def _field_label(name: str) -> str:
    return name.replace("_", " ").title()


def _render_entry(activity: ResolvedActivity) -> str:
    """Render one resolved activity as markdown (TBC if empty)."""
    if activity.is_empty:
        return "*TBC*\n"

    if activity.set is not None:
        return _render_set(activity.set)

    lines: list[str] = []
    single = len(activity.values) == 1
    for name, value in activity.values.items():
        if not value:
            continue
        # A lone element needs no label — the activity title already names it.
        lines.append(value if single else f"{_field_label(name)}: {value}")

    return ("\n".join(lines) if lines else "*TBC*") + "\n"


def _render_set(live_set: ResolvedSet) -> str:
    """Render a set-based activity's live set: name, full weapon list, armor once."""
    lines: list[str] = []
    if live_set.name:
        lines.append(f"*{live_set.name}*")
    if live_set.weapons:
        lines.append("Weapons:")
        lines.extend(f"- {weapon}" for weapon in live_set.weapons)
    if live_set.armor:
        # Armor is identical across the three classes, so name it once.
        lines.append(f"Armor: {', '.join(live_set.armor)} (all classes)")
    return "\n".join(lines) + "\n"


_FOOTER = (
    "[More on Kyber's Corner](https://kyberscorner.com/destiny2/legacy-activities/) ↗\n"
    "[Support Us](https://ko-fi.com/Kyber3000) ↗\n"
)


def _header(title: str, subtitle: str) -> str:
    return f"**Destiny 2**\n## {title} — Legacy Activities\n\n-# {subtitle}\n\n"


def _inline_values(activity: ResolvedActivity) -> str:
    """A compact one-line summary of an activity (for day/week rows)."""
    if activity.set is not None:
        return activity.set.name or "TBC"
    values = [v for v in activity.values.values() if v]
    return " — ".join(values) if values else "TBC"


async def _finish(
    header: str, body: str, *, post_name: str, emoji_dict: dict[str, h.Emoji]
) -> str:
    """Emoji-substitute and length-guard the assembled header/body/footer."""

    def sub(text: str) -> str:
        return re_user_side_emoji.sub(construct_emoji_substituter(emoji_dict), text)

    return await components.guard_cv2_post_sections(
        sub(header), sub(body), sub(_FOOTER), post_name=post_name
    )


def reset_week_start(rotation: LegacyRotation, when: dt.datetime) -> dt.datetime:
    """The weekly-reset boundary (Tuesday 17:00 UTC) on/before ``when``."""
    weeks = (when - rotation.start_date).days // 7
    return rotation.start_date + dt.timedelta(days=7 * weeks)


async def render_description(
    destination_key: str,
    resolved: list[ResolvedActivity],
    date: dt.datetime,
    *,
    emoji_dict: dict[str, h.Emoji],
) -> str:
    """Build the (emoji-substituted, length-guarded) post text for a destination/date.

    The date is shown in the header so paginated pages are self-identifying (the
    paginator's ``n/m`` indicator alone doesn't say which day/week is on screen).
    """
    title, _activities = rotation_schema.LEGACY_DESTINATIONS[destination_key]
    header = _header(title, f"Showing <t:{int(date.timestamp())}:D>")

    body = ""
    for activity in resolved:
        body += f"**{activity.title}**\n"
        body += _render_entry(activity)
        body += "\n"

    return await _finish(
        header, body, post_name=f"Legacy {title}", emoji_dict=emoji_dict
    )


def build_container(description: str) -> list[h.api.ComponentBuilder]:
    """Wrap a rendered description in a **fresh** CV2 container.

    Fresh per call by contract: :class:`.components.Paginator` injects its nav row into
    the returned container on every render, so a reused builder would accumulate rows on
    revisits — a page factory must therefore build this anew each time it is invoked.
    """
    container = h.impl.ContainerComponentBuilder(
        accent_color=h.Color(cfg.embed_default_color)
    )
    container.add_text_display(description)
    return [container]


async def render_page(
    destination_key: str,
    resolved: list[ResolvedActivity],
    date: dt.datetime,
    *,
    emoji_dict: dict[str, h.Emoji],
) -> list[h.api.ComponentBuilder]:
    """Render a destination/date to a one-off CV2 component list (non-paginated use)."""
    description = await render_description(
        destination_key, resolved, date, emoji_dict=emoji_dict
    )
    return build_container(description)


async def render_week_description(
    destination_key: str,
    rotation: LegacyRotation,
    week_start: dt.datetime,
    *,
    emoji_dict: dict[str, h.Emoji],
) -> str:
    """Render one reset-week: weekly activities once, plus a per-day breakdown of the
    daily activities across the seven days of that week."""
    title = rotation_schema.LEGACY_DESTINATIONS[destination_key][0]
    header = _header(title, f"Week of <t:{int(week_start.timestamp())}:D>")

    days = [week_start + dt.timedelta(days=i) for i in range(7)]
    per_day = [rotation(d) for d in days]
    week_activities = per_day[0]

    body = ""
    weekly = [a for a in week_activities if a.cadence == "weekly"]
    if weekly:
        for activity in weekly:
            body += f"**{activity.title}**\n" + _render_entry(activity)
        body += "\n"

    for pos, activity in enumerate(week_activities):
        if activity.cadence != "daily":
            continue
        body += f"**{activity.title}** · daily\n"
        for i, day in enumerate(days):
            body += f"<t:{int(day.timestamp())}:d>  {_inline_values(per_day[i][pos])}\n"
        body += "\n"

    return await _finish(
        header, body, post_name=f"Legacy {title}", emoji_dict=emoji_dict
    )


async def render_upcoming_description(
    destination_key: str,
    rotation: LegacyRotation,
    dates: list[dt.datetime],
    *,
    emoji_dict: dict[str, h.Emoji],
    date_style: str,
) -> str:
    """Render a single, non-paginated post listing the current + upcoming rotation.

    ``dates[0]`` is the current period (bolded); the rest are upcoming. Used for the
    short-cycle destinations that fit their whole upcoming schedule in one post.
    """
    title = rotation_schema.LEGACY_DESTINATIONS[destination_key][0]
    header = _header(title, "Current and upcoming rotation")
    per_date = [(d, rotation(d)) for d in dates]

    body = ""
    for pos, first in enumerate(per_date[0][1]):
        body += f"**{first.title}**\n"
        # For a multi-element activity, name the columns once (e.g. Fabled — Legendary).
        if first.set is None and len(first.values) > 1:
            body += "-# " + " — ".join(_field_label(n) for n in first.values) + "\n"
        for i, (day, activities) in enumerate(per_date):
            stamp = f"<t:{int(day.timestamp())}:{date_style}>"
            row = f"{stamp} · {_inline_values(activities[pos])}"
            body += (f"**{row}**" if i == 0 else row) + "\n"
        body += "\n"

    return await _finish(
        header, body, post_name=f"Legacy {title}", emoji_dict=emoji_dict
    )
