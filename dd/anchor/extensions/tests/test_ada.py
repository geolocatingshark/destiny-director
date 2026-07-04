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

"""Pure-helper tests for the Ada-1 weekly shaders autopost.

No DB / network / Discord — only the manifest-free pure functions, exercised with
hand-built ``DestinyItem`` fixtures that mirror the live shapes."""

import datetime as dt

from dd.anchor.extensions.ada import (
    _ada_shader_line,
    _inventory_changes_line,
    _render_shader_block,
    _shaders,
    next_ada_reset,
)
from dd.anchor.extensions.bungie_api.models import DestinyItem


def _shader(name: str = "Alchemy Scorch", hash_: int = 1, cost: int = 10000):
    return DestinyItem(
        name=name,
        hash_=hash_,
        rarity="Legendary",
        class_="Unknown",
        bucket="",
        item_type=19,
        item_type_friendly_name="Shader",
        costs={"Glimmer": cost},
    )


def _armor():
    return DestinyItem(
        name="Chestpiece",
        hash_=7,
        rarity="Legendary",
        class_="Titan",
        bucket="Chest",
        item_type=2,
        item_type_friendly_name="Chest Armor",
    )


def _misc():
    # A synthesis material / mod: neither a shader nor armour.
    return DestinyItem(
        name="Synthweave Template",
        hash_=99,
        rarity="Legendary",
        class_="Unknown",
        bucket="",
        item_type=0,
        item_type_friendly_name="Material",
    )


def test_shaders_keeps_only_shaders():
    shaders = _shaders([_shader(), _armor(), _misc()])
    assert [s.name for s in shaders] == ["Alchemy Scorch"]


def test_shader_line_is_bold_with_emoji_and_link_and_no_cost():
    line = _ada_shader_line(_shader(name="Cryptic Legacy", hash_=42, cost=10000))
    assert line.startswith("🎨 ")
    assert "[**Cryptic Legacy**]" in line  # bold name
    assert "https://light.gg/db/items/42" in line
    assert "—" not in line  # cost is no longer shown
    assert "Glimmer" not in line


def test_render_shader_block_sorted_by_name():
    block = _render_shader_block([_shader("Zebra", 1), _shader("Apple", 2)])
    assert block.index("Apple") < block.index("Zebra")


def test_render_shader_block_empty():
    assert "No shaders" in _render_shader_block([])


def test_next_ada_reset_midweek_returns_upcoming_tuesday_1700_utc():
    # Saturday 2026-07-04 12:00 UTC → Tuesday 2026-07-07 17:00 UTC.
    now = dt.datetime(2026, 7, 4, 12, 0, tzinfo=dt.UTC)
    assert next_ada_reset(now) == dt.datetime(2026, 7, 7, 17, 0, tzinfo=dt.UTC)


def test_next_ada_reset_at_reset_moment_rolls_to_next_week():
    # At reset (Tue 17:00) the stock is fresh; the next change is a week out.
    now = dt.datetime(2026, 7, 7, 17, 0, tzinfo=dt.UTC)
    assert next_ada_reset(now) == dt.datetime(2026, 7, 14, 17, 0, tzinfo=dt.UTC)


def test_next_ada_reset_just_before_reset_is_same_day():
    now = dt.datetime(2026, 7, 7, 16, 59, tzinfo=dt.UTC)
    assert next_ada_reset(now) == dt.datetime(2026, 7, 7, 17, 0, tzinfo=dt.UTC)


def test_inventory_changes_line_is_discord_timestamp():
    now = dt.datetime(2026, 7, 4, 12, 0, tzinfo=dt.UTC)
    unix = int(dt.datetime(2026, 7, 7, 17, 0, tzinfo=dt.UTC).timestamp())
    assert _inventory_changes_line(now) == f"Inventory changes: <t:{unix}:f>"
