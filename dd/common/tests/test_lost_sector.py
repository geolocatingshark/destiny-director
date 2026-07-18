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

"""The Lost Sector post body builder (shared by the live post + preview wall). Pure."""

import typing as t

from dd.common import lost_sector


class _StubSector:
    """A stand-in Sector — build_body reads only .name and .shortlink_gfx here."""

    def __init__(self, name: str, shortlink_gfx: str) -> None:
        self.name = name
        self.shortlink_gfx = shortlink_gfx


def _sectors(*pairs: tuple[str, str]) -> list:
    """A stub sector list typed as a bare ``list`` so build_body accepts it."""
    return t.cast("list", [_StubSector(name, link) for name, link in pairs])


def test_build_body_without_details_lists_sectors_between_header_and_footer():
    sectors = _sectors(("Perdition", "https://kyber3000.com/ls/perdition"))
    body = lost_sector.build_body(sectors, details_enabled=False)
    # Header (title as a ## heading) and the raw :emoji: reward footer are present.
    assert "## [World Lost Sectors](https://kyber3000.com/LS)" in body
    assert ":enhancement_core: Enhancement Core" in body
    # Each sector is a :LS:-prefixed masked link with raw tokens (previewer renders it).
    assert ":LS: **[Perdition](https://kyber3000.com/ls/perdition)**" in body
    # details disabled -> no Champions/Shields block.
    assert "Champions:" not in body


def test_build_body_orders_header_sectors_footer():
    sectors = _sectors(("A", "https://x/a"), ("B", "https://x/b"))
    body = lost_sector.build_body(sectors, details_enabled=False)
    assert body.index("World Lost Sectors") < body.index("[A]") < body.index("[B]")
    assert body.index("[B]") < body.index("Enhancement Core")
