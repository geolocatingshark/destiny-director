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

"""Tests for rebuild_components (CV2 component *models* -> sendable *builders*).

Component models are simulated with ``MagicMock(spec=…)`` so ``isinstance`` dispatch in
``_rebuild_component`` picks the right branch; assertions are on the rebuilt builder's
type + shape (no Discord I/O).
"""

from unittest.mock import MagicMock

import hikari as h
import pytest

from dd.common import components


def _text(content: str) -> MagicMock:
    m = MagicMock(spec=h.TextDisplayComponent)
    m.content = content
    return m


def _gallery_item(url: str, *, description: str | None = None, spoiler: bool = False):
    it = MagicMock()
    it.media.url = url
    it.description = description
    it.is_spoiler = spoiler
    return it


def _button(
    *,
    custom_id: str | None = None,
    url: str | None = None,
    label: str = "L",
    style: h.ButtonStyle = h.ButtonStyle.PRIMARY,
    emoji=None,
    is_disabled: bool = False,
) -> MagicMock:
    b = MagicMock(spec=h.ButtonComponent)
    b.custom_id = custom_id
    b.url = url
    b.label = label
    b.style = style
    b.emoji = emoji
    b.is_disabled = is_disabled
    return b


def test_rebuild_media_gallery():
    model = MagicMock(spec=h.MediaGalleryComponent)
    model.items = [
        _gallery_item("https://x/1.png", description="d", spoiler=True),
        _gallery_item("https://x/2.png"),
    ]
    [builder] = components.rebuild_components([model])
    assert isinstance(builder, h.impl.MediaGalleryComponentBuilder)
    assert len(builder.items) == 2


def test_rebuild_file():
    model = MagicMock(spec=h.FileComponent)
    model.file.url = "https://x/a.txt"
    model.is_spoiler = False
    [builder] = components.rebuild_components([model])
    assert isinstance(builder, h.impl.FileComponentBuilder)


def test_rebuild_section_with_thumbnail_accessory():
    thumb = MagicMock(spec=h.ThumbnailComponent)
    thumb.media.url = "https://x/t.png"
    thumb.description = None
    thumb.is_spoiler = False
    model = MagicMock(spec=h.SectionComponent)
    model.accessory = thumb
    model.components = [_text("hi"), _text("there")]
    [builder] = components.rebuild_components([model])
    assert isinstance(builder, h.impl.SectionComponentBuilder)
    assert isinstance(builder.accessory, h.impl.ThumbnailComponentBuilder)
    assert len(builder.components) == 2


def test_rebuild_action_row_interactive_and_link_buttons():
    row = MagicMock(spec=h.ActionRowComponent)
    row.components = [
        _button(custom_id="go", label="Go"),
        _button(url="https://x", label="Site"),
    ]
    [builder] = components.rebuild_components([row])
    assert isinstance(builder, h.impl.MessageActionRowBuilder)
    assert len(builder.components) == 2
    assert isinstance(builder.components[0], h.impl.InteractiveButtonBuilder)
    assert isinstance(builder.components[1], h.impl.LinkButtonBuilder)


def test_rebuild_container_with_media_gallery_child():
    gallery = MagicMock(spec=h.MediaGalleryComponent)
    gallery.items = [_gallery_item("https://x/1.png")]
    container = MagicMock(spec=h.ContainerComponent)
    container.accent_color = None
    container.is_spoiler = False
    container.components = [_text("head"), gallery]
    [builder] = components.rebuild_components([container])
    assert isinstance(builder, h.impl.ContainerComponentBuilder)
    assert len(builder.components) == 2


def test_rebuild_unsupported_component_raises():
    # A component that matches none of the handled kinds surfaces loudly (the mirror
    # relies on this; the navigator catches it and degrades to a no-data page).
    with pytest.raises(NotImplementedError):
        components.rebuild_components([object()])  # ty: ignore[invalid-argument-type]


def test_rebuild_action_row_with_non_button_child_raises():
    row = MagicMock(spec=h.ActionRowComponent)
    row.components = [object()]  # e.g. a select menu
    with pytest.raises(NotImplementedError):
        components.rebuild_components([row])
