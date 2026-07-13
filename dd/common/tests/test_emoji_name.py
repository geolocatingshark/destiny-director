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

# Unit tests for emoji_name(): every output must satisfy Discord's verified emoji-name
# rule ^[A-Za-z0-9_]{2,32}$, and the transform must handle spaces, disallowed symbols,
# accents, and over-long / empty names deterministically. Pure function, no DB.

import re

import pytest

from dd.common.emoji_store import emoji_name

_DISCORD_RULE = re.compile(r"^[A-Za-z0-9_]{2,32}$")

# (input, expected) pairs drawn from real Destiny item names, incl. the awkward ones.
CASES = [
    ("Enigma's Draw", "enigma_s_draw"),
    ("Legal Action II", "legal_action_ii"),
    ("Arsenic Bite-4b", "arsenic_bite_4b"),
    ("Steel Sybil Z-14", "steel_sybil_z_14"),
    ("Drang (Baroque)", "drang_baroque"),
    ("The Last Dance", "the_last_dance"),
    ("Chrysura Melo", "chrysura_melo"),
    # accents transliterate to ASCII
    ("Café Racer", "cafe_racer"),
    # long name truncates to 32 chars, no trailing underscore
    ("Cuirass of the Emperor's Champion", "cuirass_of_the_emperor_s_champio"),
]


@pytest.mark.parametrize(("raw", "expected"), CASES)
def test_emoji_name_expected(raw: str, expected: str) -> None:
    assert emoji_name(raw) == expected


@pytest.mark.parametrize(("raw", "_expected"), CASES)
def test_emoji_name_always_valid(raw: str, _expected: str) -> None:
    assert _DISCORD_RULE.match(emoji_name(raw))


@pytest.mark.parametrize(
    "raw",
    [
        "",  # empty -> 'item'
        "!!!",  # all-symbols -> collapses to empty -> 'item'
        "x",  # single char -> padded to length 2
        "é",  # single non-ascii -> transliterates then pads
        "  ---  ",  # only separators
        "A" * 60,  # very long
        "<:already:123>",  # angle/colon soup still yields a valid slug
    ],
)
def test_emoji_name_edge_inputs_are_valid(raw: str) -> None:
    out = emoji_name(raw)
    assert _DISCORD_RULE.match(out), f"{raw!r} -> {out!r} violates the emoji-name rule"


def test_emoji_name_empty_and_symbol_only_fall_back_to_item() -> None:
    assert emoji_name("") == "item"
    assert emoji_name("!!!") == "item"


def test_emoji_name_is_deterministic() -> None:
    assert emoji_name("Whispering Slab") == emoji_name("Whispering Slab")
