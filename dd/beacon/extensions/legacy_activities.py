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

"""Beacon read commands for the legacy world-activity rotations.

One top-level command per destination (``/neomuna``, ``/moon``, ``/dares``, …). Each
destination renders in one of three modes:

- **single** (``rahool``, ``pale_heart``, ``kepler``): short cycles, so a single
  non-paginated post lists the current + upcoming rotation.
- **week-daily** (``neomuna``, ``moon``): a *weekly* navigator; each page shows the
  week's weekly activities once plus a per-day breakdown of the daily activities.
- **navigator** (everything else): a date-navigable navigator (one page per day for a
  daily destination, per week for a weekly one).

All modes are built on :class:`dd.common.components.Paginator` (or a one-off response)
— no followable channel, no autoposts.
"""

import datetime as dt
import typing as t

import hikari as h
import lightbulb as lb

from ...common import components
from ...common.bot import ServerEmojiEnabledBot
from ...common.components import cv2_error, respond_cv2
from ...common.legacy_activities import (
    build_container,
    load_rotation,
    render_description,
    render_upcoming_description,
    render_week_description,
    reset_week_start,
)
from ...common.rotation_schema import LEGACY_DESTINATIONS
from ...sector_accounting.legacy_activities import LegacyRotation

loader = lb.Loader()

# Destinations whose short cycles fit their whole upcoming schedule in one post.
_SINGLE = frozenset({"rahool", "pale_heart", "kepler"})
# Mixed daily+weekly destinations shown as a weekly navigator with a daily breakdown.
_WEEK_DAILY = frozenset({"neomuna", "moon"})

# How many pages / rows each mode shows.
_DAILY_PAGE_COUNT = 14  # navigator, daily destination (two weeks)
_WEEKLY_PAGE_COUNT = 8  # navigator, weekly destination (two months)
_WEEK_DAILY_PAGE_COUNT = 8  # week-daily navigator (two months of weeks)
_SINGLE_DAILY_COUNT = 8  # single mode, daily destination (rows)
_SINGLE_WEEKLY_COUNT = 6  # single mode, weekly destination (rows)


async def build_pages(
    destination_key: str,
    rotation: LegacyRotation,
    emoji_dict: dict[str, h.Emoji],
    *,
    now: dt.datetime,
) -> list[components.Page]:
    """A navigator's forward window of per-date pages (day or week per the cadence).

    The async post text is computed once up front, but each factory builds a **fresh**
    container per call — the Paginator injects its nav row into the returned container
    on every render, so a reused builder would pile up rows on revisits (paging back).
    """
    step = rotation.step
    page_count = _DAILY_PAGE_COUNT if step.days == 1 else _WEEKLY_PAGE_COUNT

    pages: list[components.Page] = []
    for offset in range(page_count):
        date = now + step * offset
        description = await render_description(
            destination_key, rotation(date), date, emoji_dict=emoji_dict
        )
        pages.append(lambda desc=description: build_container(desc))
    return pages


async def build_week_pages(
    destination_key: str,
    rotation: LegacyRotation,
    emoji_dict: dict[str, h.Emoji],
    *,
    now: dt.datetime,
) -> list[components.Page]:
    """A weekly navigator (page 1 = the current reset week) with a daily breakdown."""
    week0 = reset_week_start(rotation, now)
    pages: list[components.Page] = []
    for offset in range(_WEEK_DAILY_PAGE_COUNT):
        week_start = week0 + dt.timedelta(days=7 * offset)
        description = await render_week_description(
            destination_key, rotation, week_start, emoji_dict=emoji_dict
        )
        pages.append(lambda desc=description: build_container(desc))
    return pages


def make_legacy_command(
    destination_key: str, name: str, description: str
) -> type[lb.SlashCommand]:
    """Build an (unregistered) top-level ``/<destination>`` command."""

    class _LegacyCommand(lb.SlashCommand, name=name, description=description):
        @lb.invoke
        async def invoke(self, ctx: lb.Context) -> None:
            bot = t.cast(ServerEmojiEnabledBot, ctx.client.app)

            try:
                rotation = await load_rotation(destination_key)
            except Exception:
                await respond_cv2(
                    ctx,
                    cv2_error(
                        "No data yet",
                        f"The {name} rotation hasn't been set up yet.",
                    ),
                    ephemeral=True,
                )
                return

            emoji = bot.emoji
            now = dt.datetime.now(tz=dt.UTC)

            if destination_key in _SINGLE:
                if rotation.step.days == 1:  # daily
                    dates = [
                        now + dt.timedelta(days=i) for i in range(_SINGLE_DAILY_COUNT)
                    ]
                    style = "d"
                else:  # weekly — align rows to the reset boundary
                    week0 = reset_week_start(rotation, now)
                    dates = [
                        week0 + dt.timedelta(days=7 * i)
                        for i in range(_SINGLE_WEEKLY_COUNT)
                    ]
                    style = "D"
                description = await render_upcoming_description(
                    destination_key, rotation, dates, emoji_dict=emoji, date_style=style
                )
                await ctx.respond(
                    components=build_container(description),
                    flags=h.MessageFlag.IS_COMPONENTS_V2,
                )
                return

            if destination_key in _WEEK_DAILY:
                pages = await build_week_pages(
                    destination_key, rotation, emoji, now=now
                )
            else:
                pages = await build_pages(destination_key, rotation, emoji, now=now)
            await components.Paginator(pages).send(ctx)

    return _LegacyCommand


for _key, (_title, _activities) in LEGACY_DESTINATIONS.items():
    loader.command(
        make_legacy_command(
            _key,
            name=_key.replace("_", "-"),
            description=f"{_title} legacy activity rotation",
        )
    )
