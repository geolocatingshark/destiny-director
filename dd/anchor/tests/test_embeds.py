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

"""Unit tests for the embed builder's pure pieces (no Discord I/O).

The interactive menu/modal flow is verified manually on dev; here we exercise the field
specs, the per-field mutators, and the modal's input construction.
"""

import hikari as h
import pytest

from dd.anchor import embeds

# --- pure field specs ------------------------------------------------------------


def test_author_fields_empty_and_populated():
    assert embeds._author_fields(h.Embed()) == [
        ("Author", "", False, False),
        ("Icon URL", "", False, False),
        ("Author URL", "", False, False),
    ]
    e = h.Embed()
    e.set_author(name="Kyber", url="https://k")
    specs = embeds._author_fields(e)
    assert specs[0] == ("Author", "Kyber", False, False)
    assert specs[2] == ("Author URL", "https://k", False, False)


def test_footer_fields():
    e = h.Embed()
    e.set_footer("hi")
    specs = embeds._footer_fields(e)
    assert specs[0] == ("Footer", "hi", False, False)
    assert len(specs) == 2


# --- mutators --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mutate_title_sets_and_clears():
    e = h.Embed()
    await embeds._mutate_title(e, ["Hello"])
    assert e.title == "Hello"
    await embeds._mutate_title(e, [""])
    assert e.title is None


@pytest.mark.asyncio
async def test_mutate_color_valid_invalid_and_empty():
    e = h.Embed()
    out = await embeds._mutate_color(e, ["#ff0000"])
    assert out is not None and out.color == h.Color(0xFF0000)
    # Invalid / empty → "no change" (None) and the embed is untouched.
    assert await embeds._mutate_color(e, ["not-a-color"]) is None
    assert await embeds._mutate_color(e, [""]) is None
    assert e.color == h.Color(0xFF0000)


@pytest.mark.asyncio
async def test_mutate_author_set_and_clear():
    e = h.Embed()
    await embeds._mutate_author(e, ["Name", "", "https://u"])
    assert e.author is not None and e.author.name == "Name"
    await embeds._mutate_author(e, ["", "", ""])
    assert e.author is None


@pytest.mark.asyncio
async def test_mutate_footer_set_and_clear():
    e = h.Embed()
    await embeds._mutate_footer(e, ["Foot", ""])
    assert e.footer is not None and e.footer.text == "Foot"
    await embeds._mutate_footer(e, ["", ""])
    assert e.footer is None


@pytest.mark.asyncio
async def test_mutate_image_follows_link_and_clears(monkeypatch):
    async def _fake_follow(url: str) -> str:
        return url + "?resolved"

    monkeypatch.setattr(embeds, "follow_link_single_step", _fake_follow)

    e = h.Embed()
    await embeds._mutate_image(e, ["https://x/i.png"])
    assert e.image is not None and e.image.url == "https://x/i.png?resolved"
    await embeds._mutate_image(e, [""])
    assert e.image is None


@pytest.mark.asyncio
async def test_substitute_user_side_emoji_dict_path_passthrough():
    # The dict branch does no I/O; text with no :emoji: tokens passes through.
    assert await embeds.substitute_user_side_emoji({}, "plain text") == "plain text"


# --- modal field construction ----------------------------------------------------


async def _noop_mutate(embed: h.Embed, values: list[str]) -> h.Embed | None:
    return embed


def test_properties_modal_builds_short_and_paragraph_inputs():
    modal = embeds._PropertiesModal(
        field_specs=[("Title", "cur", False, False), ("Body", "", False, True)],
        embed=h.Embed(),
        mutate=_noop_mutate,
    )
    assert len(modal._fields) == 2
    assert modal._fields[0].style == h.TextInputStyle.SHORT
    assert modal._fields[1].style == h.TextInputStyle.PARAGRAPH
    assert modal._fields[0].label == "Title"
    assert modal._fields[0].required is False
