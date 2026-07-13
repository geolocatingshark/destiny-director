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

# CV2 section rendering of the legacy posts (dates, weapon/armor emoji, layout). Pure —
# synthetic rotations, no DB or network.

import datetime as dt
import typing as t

import hikari as h

from dd.common import components
from dd.common.legacy_activities import (
    period_starts,
    render_dares_sections,
    render_date_sections,
    render_upcoming_sections,
    render_week_sections,
    reset_week_start,
)
from dd.sector_accounting.legacy_activities import LegacyRotation

# 2026-04-21 is a Tuesday (weekly reset); 18:00 is comfortably after the 17:00 reset.
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
                    {"name": "weapon", "values": ["Circular Logic (Auto Rifle)"]},
                    {"name": "location", "values": ["Zephyr"]},
                ],
            },
            {
                "key": "story_mission",
                "title": "Story Mission",
                "cadence": "weekly",
                "elements": [{"name": "mission", "values": []}],  # exercises TBC
            },
        ],
    }
    return LegacyRotation.from_json(doc)


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
                            "Chroma Rush (Auto Rifle)",
                            "Vulpecula (Hand Cannon)",
                        ],
                        "armor": ["Wild Hunt", "Scatterhorn"],
                    }
                ],
            }
        ],
    }
    return LegacyRotation.from_json(doc)


def _rahool_rotation() -> LegacyRotation:
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
    return LegacyRotation.from_json(doc)


def test_dates_are_year_less_and_static():
    text = "\n".join(
        render_date_sections("neomuna", _neomuna_rotation()(DATE), DATE, emoji_dict={})
    )
    assert "Apr 21" in text  # Mmm DD, no year
    assert "2026" not in text  # no year anywhere
    assert "<t:" not in text  # not a locale/numeric Discord timestamp


def test_date_sections_title_and_weapon_emoji():
    sections = render_date_sections(
        "neomuna", _neomuna_rotation()(DATE), DATE, emoji_dict={}
    )
    text = "\n".join(sections)
    assert sections[0].startswith("# Neomuna")  # h1 title, consistent across posts
    # Weapon tagged with its type emoji, and the "(Auto Rifle)" suffix dropped.
    assert ":auto_rifle: Circular Logic" in text
    assert "(Auto Rifle)" not in text
    # Empty weekly activity still shows as TBC.
    assert "Story Mission" in text and "TBC" in text


def test_dares_sections_layout_emoji_and_rounds():
    doc: dict[str, t.Any] = {
        "version": 1,
        "reference_date": "2026-04-21",
        "activities": [
            {
                "key": "rounds",
                "title": "Encounter Rounds",
                "cadence": "weekly",
                "elements": [
                    {"name": "first", "values": ["Fallen"]},
                    {"name": "second", "values": ["Hive"]},
                    {"name": "final", "values": ["Zydron (Vex)"]},
                ],
            },
            {
                "key": "loot_table",
                "title": "Legendary Loot",
                "cadence": "weekly",
                "kind": "sets",
                "schedule": ["Set 1"],
                "sets": [
                    {
                        "name": "Set 1",
                        "weapons": ["Chroma Rush (Auto Rifle)"],
                        "armor": ["Wild Hunt", "Scatterhorn"],
                    }
                ],
            },
        ],
    }
    rot = LegacyRotation.from_json(doc)
    sections = render_dares_sections(rot(DATE), DATE, emoji_dict={})
    text = "\n".join(sections)
    assert "Dares 𝑜𝑓 Eternity" in text
    # Expert rounds as an emoji-prefixed arrow chain.
    assert ":30th_annv: Fallen⇢ Hive⇢ Zydron (Vex)" in text
    assert "Legendary Armor // Set 1" in text
    assert ":armor: Wild Hunt" in text and ":armor: Scatterhorn" in text
    assert "available for all classes" in text
    assert "Legendary Weapons // Set 1" in text
    assert ":auto_rifle: Chroma Rush" in text
    assert "View more details" in text
    container = components.build_container(sections)
    assert components.cv2_text_length([container]) <= components.CV2_TEXT_LIMIT


def test_week_sections_weekly_once_daily_per_day():
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
                "key": "altar",
                "title": "Altar of Sorrow",
                "cadence": "daily",
                "elements": [
                    {"name": "weapon", "values": ["Heretic (Rocket Launcher)"]},
                    {"name": "boss", "values": ["Nightmare of Zydron"]},
                ],
            },
        ],
    }
    rot = LegacyRotation.from_json(doc)
    sections = render_week_sections(
        "moon", rot, reset_week_start(rot, DATE), emoji_dict={}
    )
    text = "\n".join(sections)
    assert "Week of Apr 21" in text
    assert text.count("Vex Incursion Zone") == 1  # weekly shown once
    assert "Altar of Sorrow" in text and "· daily" in text
    assert text.count(" · :rocket_launcher: Heretic · Nightmare of Zydron") == 7
    assert "2026" not in text and "<t:" not in text


def test_upcoming_a_look_ahead_format():
    rot = _rahool_rotation()
    dates = period_starts(rot, DATE, 4)  # current + 3 upcoming
    text = "\n".join(
        render_upcoming_sections("rahool", rot, dates, emoji_dict={}, armor=True)
    )
    assert text.startswith("# Rahool's Armor Focus")  # activity title as the h1
    assert "*`A Look Ahead`*" in text
    assert f"Resets <t:{int(dates[1].timestamp())}:R>" in text  # live countdown
    assert "*now*" in text  # the current period is marked
    assert "▸ :armor: Arms" in text  # current value, armor-tagged
    assert "(sequence repeats)" in text
    assert "2026" not in text  # year-less dates


def test_emoji_tokens_are_substituted():
    emoji = h.CustomEmoji(id=h.Snowflake(1), name="armor", is_animated=False)
    sections = render_upcoming_sections(
        "rahool",
        _rahool_rotation(),
        period_starts(_rahool_rotation(), DATE, 3),
        emoji_dict={"armor": emoji},
        armor=True,
    )
    text = "\n".join(sections)
    assert emoji.mention in text  # :armor: resolved to the guild emoji


def test_weapon_links_render_when_baked():
    from dd.common.legacy_activities import render_dares_sections

    rot = (
        _dares_rotation()
    )  # weapons: Chroma Rush (Auto Rifle), Vulpecula (Hand Cannon)
    links = {"Chroma Rush (Auto Rifle)": "https://www.light.gg/db/items/100/"}
    text = "\n".join(render_dares_sections(rot(DATE), DATE, emoji_dict={}, links=links))
    # Linked weapon: emoji + [Name](url); the "(Type)" suffix is dropped.
    assert ":auto_rifle: [Chroma Rush](https://www.light.gg/db/items/100/)" in text
    # Un-baked weapon stays plain (emoji + name, no link).
    assert ":hand_cannon: Vulpecula" in text
    assert "[Vulpecula]" not in text


def test_weapon_values_extracts_only_weapons():
    from dd.common.legacy_activities import weapon_values

    doc: dict[str, t.Any] = {
        "activities": [
            {
                "key": "loot_table",
                "kind": "sets",
                "sets": [
                    {
                        "name": "Set 1",
                        "weapons": ["Chroma Rush (Auto Rifle)"],
                        "armor": ["Wild Hunt"],
                    }
                ],
            },
            {
                "key": "wellspring",
                "elements": [
                    {"name": "weapon", "values": ["Tarnation (Grenade Launcher)"]},
                    {"name": "boss", "values": ["Golmag"]},
                ],
            },
        ],
    }
    found = weapon_values(doc)
    assert found == {"Chroma Rush (Auto Rifle)", "Tarnation (Grenade Launcher)"}
    assert "Wild Hunt" not in found and "Golmag" not in found
