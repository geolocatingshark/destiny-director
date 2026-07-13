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

# CV2 rendering of a legacy destination page. No DB (synthetic rotation), no network.

import datetime as dt
import typing as t

import hikari as h
import pytest

from dd.common import components
from dd.common.legacy_activities import (
    render_page,
    render_upcoming_description,
    render_week_description,
    reset_week_start,
)
from dd.sector_accounting.legacy_activities import LegacyRotation

pytestmark = pytest.mark.asyncio

DATE = dt.datetime(2026, 4, 21, 18, tzinfo=dt.UTC)


def _neomuna_rotation() -> LegacyRotation:
    doc: dict[str, t.Any] = {
        "version": 1,
        "reference_date": "2026-04-21",
        "activities": [
            {
                "key": "terminal_overload",
                "title": "Terminal Overload",
                "cadence": "daily",
                "elements": [
                    {"name": "weapon", "values": [":arc: Circular Logic"]},
                    {"name": "location", "values": ["Zephyr"]},
                ],
            },
            {
                "key": "story_mission",
                "title": "Story Mission",
                "cadence": "weekly",
                # empty values exercises the TBC path
                "elements": [{"name": "mission", "values": []}],
            },
        ],
    }
    return LegacyRotation.from_json(doc)


def _text(comps: list[h.api.ComponentBuilder]) -> str:
    container = t.cast(h.impl.ContainerComponentBuilder, comps[0])
    display = t.cast(h.impl.TextDisplayComponentBuilder, container.components[0])
    return display.content


async def test_render_returns_single_container():
    comps = await render_page("neomuna", _neomuna_rotation()(DATE), DATE, emoji_dict={})
    assert len(comps) == 1
    assert isinstance(comps[0], h.impl.ContainerComponentBuilder)


async def test_render_within_cv2_budget():
    comps = await render_page("neomuna", _neomuna_rotation()(DATE), DATE, emoji_dict={})
    assert components.cv2_text_length(comps) <= components.CV2_TEXT_LIMIT


async def test_render_includes_date_header_and_title():
    comps = await render_page("neomuna", _neomuna_rotation()(DATE), DATE, emoji_dict={})
    text = _text(comps)
    assert "Neomuna" in text
    assert f"<t:{int(DATE.timestamp())}:D>" in text


async def test_render_labels_multi_element_activity():
    comps = await render_page("neomuna", _neomuna_rotation()(DATE), DATE, emoji_dict={})
    text = _text(comps)
    assert "Location: Zephyr" in text


async def test_render_marks_empty_activity_tbc():
    comps = await render_page("neomuna", _neomuna_rotation()(DATE), DATE, emoji_dict={})
    text = _text(comps)
    assert "Story Mission" in text
    assert "TBC" in text


def _dares_rotation() -> LegacyRotation:
    doc: dict[str, t.Any] = {
        "version": 1,
        "reference_date": "2026-04-21",
        "activities": [
            {
                "key": "loot_table",
                "title": "Legendary Loot",
                "cadence": "weekly",
                "kind": "sets",
                "schedule": ["Set 1"],
                "sets": [
                    {
                        "name": "Set 1",
                        "weapons": [
                            "Enigmas's Draw (Sidearm)",
                            "Dire Promise (Hand Cannon)",
                        ],
                        "armor": ["Wild Hunt", "Scatterhorn"],
                    }
                ],
            }
        ],
    }
    return LegacyRotation.from_json(doc)


async def test_render_set_based_activity():
    comps = await render_page("dares", _dares_rotation()(DATE), DATE, emoji_dict={})
    text = _text(comps)
    assert "Set 1" in text
    assert "- Enigmas's Draw (Sidearm)" in text  # full weapon list, bulleted
    assert "Armor: Wild Hunt, Scatterhorn (all classes)" in text  # named once
    assert components.cv2_text_length(comps) <= components.CV2_TEXT_LIMIT


async def test_render_substitutes_known_emoji():
    emoji = h.CustomEmoji(id=h.Snowflake(1), name="arc", is_animated=False)
    comps = await render_page(
        "neomuna", _neomuna_rotation()(DATE), DATE, emoji_dict={"arc": emoji}
    )
    text = _text(comps)
    # The token is replaced by the custom-emoji mention (which itself contains :arc:).
    assert emoji.mention in text
    assert "Weapon: :arc:" not in text


def _mixed_rotation() -> LegacyRotation:
    # A weekly activity beside a daily one (Neomuna-shaped).
    doc: dict[str, t.Any] = {
        "version": 1,
        "reference_date": "2026-04-21",
        "activities": [
            {
                "key": "vex_incursion",
                "title": "Vex Incursion Zone",
                "cadence": "weekly",
                "elements": [{"name": "zone", "values": ["Ahimsa Park", "Liming"]}],
            },
            {
                "key": "terminal_overload",
                "title": "Terminal Overload",
                "cadence": "daily",
                "elements": [
                    {"name": "weapon", "values": ["W1", "W2", "W3"]},
                    {"name": "location", "values": ["L1", "L2", "L3"]},
                ],
            },
        ],
    }
    return LegacyRotation.from_json(doc)


async def test_render_week_description_weekly_once_daily_per_day():
    rot = _mixed_rotation()
    week_start = reset_week_start(rot, DATE)  # DATE is 2026-04-21 18:00
    text = await render_week_description("neomuna", rot, week_start, emoji_dict={})
    assert "Week of" in text
    # Weekly activity shown once for the week.
    assert text.count("Vex Incursion Zone") == 1
    assert "Ahimsa Park" in text
    # Daily activity broken out across the seven days (7 dated rows).
    assert "Terminal Overload" in text and "· daily" in text
    assert "W1 — L1" in text  # daily values rendered inline (weapon — location)
    assert text.count(":d>") == 7  # exactly the seven day rows


async def test_render_upcoming_bolds_current_and_lists_rest():
    doc: dict[str, t.Any] = {
        "version": 1,
        "reference_date": "2026-04-21",
        "activities": [
            {
                "key": "rahool_focus",
                "title": "Rahool's Armor Focus",
                "cadence": "daily",
                "elements": [{"name": "slot", "values": ["Arms", "Chest", "Helmet"]}],
            }
        ],
    }
    rot = LegacyRotation.from_json(doc)
    dates = [DATE + dt.timedelta(days=i) for i in range(4)]
    text = await render_upcoming_description(
        "rahool", rot, dates, emoji_dict={}, date_style="d"
    )
    assert "Current and upcoming rotation" in text
    assert text.count("**<t:") == 1  # exactly the current row is bolded
    assert text.count(":d>") == 4  # one row per requested date
