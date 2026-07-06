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

"""Unit tests for NavPages._finalize_cv2 and _cv2_text_length (no Discord I/O)."""

import hikari as h

from dd.beacon.nav import NavPages, _cv2_text_length
from dd.common import components as dd_components
from dd.hmessage import HMessage


def _bare_pages(*, cv2: bool = True) -> NavPages:
    """A NavPages with only the attributes _finalize_cv2 reads (it is pure)."""
    pages = NavPages.__new__(NavPages)
    pages.cv2 = cv2
    pages.no_data_message = HMessage(
        components=[dd_components.build_container(["No data here!"])]
    )
    return pages


def _container(text: str) -> h.impl.ContainerComponentBuilder:
    c = h.impl.ContainerComponentBuilder()
    c.add_text_display(text)
    return c


# --- _cv2_text_length ------------------------------------------------------------


def test_cv2_text_length_counts_astral_chars_as_two():
    # Discord counts CV2 text in UTF-16 units, so a non-BMP glyph counts as 2, not 1.
    container = h.impl.ContainerComponentBuilder()
    container.add_text_display("💀ab")  # 1 astral (2 UTF-16 units) + 2 ascii = 4
    assert _cv2_text_length([container]) == 4


def test_cv2_text_length_sums_nested_text():
    container = h.impl.ContainerComponentBuilder()
    container.add_text_display("12345")  # 5
    section = h.impl.SectionComponentBuilder(
        accessory=h.impl.ThumbnailComponentBuilder(media="https://x/y.png")
    )
    section.add_text_display("abc")  # 3 (nested inside a section)
    container.add_component(section)

    assert _cv2_text_length([container]) == 8


# --- _finalize_cv2 ---------------------------------------------------------------


def test_embed_page_is_converted_to_cv2():
    pages = _bare_pages(cv2=True)
    result = pages._finalize_cv2(HMessage(embeds=[h.Embed(title="t", description="d")]))

    assert len(result.components) == 1
    assert isinstance(result.components[0], h.impl.ContainerComponentBuilder)
    assert result.components[0].components  # non-empty container
    assert result.embeds == []  # the source embed is not carried over


def test_embed_page_drops_remote_image_media():
    # A converted history page must not carry a streamed remote image (the navigator
    # re-renders often; re-downloading a third-party host 429s — see /ls incident).
    pages = _bare_pages(cv2=True)
    embed = h.Embed(description="lost sector")
    embed.set_image("https://kyberscorner.com/ls.gif")
    result = pages._finalize_cv2(HMessage(embeds=[embed]))

    container = result.components[0]
    assert isinstance(container, h.impl.ContainerComponentBuilder)
    assert not any(
        isinstance(c, h.impl.MediaGalleryComponentBuilder) for c in container.components
    )


def test_native_cv2_page_passes_through():
    pages = _bare_pages(cv2=True)
    native = _container("live")
    result = pages._finalize_cv2(HMessage(components=[native]))

    assert result.components == [native]  # same container object, no conversion


def test_mixed_bin_keeps_native_then_appends_converted():
    pages = _bare_pages(cv2=True)
    native = _container("live")
    result = pages._finalize_cv2(
        HMessage(components=[native], embeds=[h.Embed(title="old", description="post")])
    )

    assert len(result.components) == 2
    assert result.components[0] is native  # native first
    assert isinstance(result.components[1], h.impl.ContainerComponentBuilder)


def test_empty_embed_falls_back_to_no_data():
    pages = _bare_pages(cv2=True)
    # An embed with nothing embeds_to_container would render -> empty container.
    result = pages._finalize_cv2(HMessage(embeds=[h.Embed()]))

    assert result is pages.no_data_message


def test_cv2_false_navigator_is_untouched():
    pages = _bare_pages(cv2=False)
    msg = HMessage(embeds=[h.Embed(title="t", description="d")])
    result = pages._finalize_cv2(msg)

    assert result is msg  # passthrough, embeds intact
    assert result.components == []


def test_oversized_page_is_truncated_to_fit_the_cap():
    pages = _bare_pages(cv2=True)
    result = pages._finalize_cv2(HMessage(embeds=[h.Embed(description="x" * 8000)]))

    # The converted page is trimmed under Discord's hard 4000-char cap (Discord would
    # otherwise reject it) and carries a visible truncation note.
    assert _cv2_text_length(result.components) <= 4000
    text = " ".join(
        c.content
        for cont in result.components
        if isinstance(cont, h.impl.ContainerComponentBuilder)
        for c in cont.components
        if isinstance(c, h.impl.TextDisplayComponentBuilder)
    )
    assert "truncated" in text
