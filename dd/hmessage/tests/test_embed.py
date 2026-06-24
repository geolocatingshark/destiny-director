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

# Pure tests for MultiImageEmbedList — splits a set of image URLs across embeds
# that share one canonical url.

import hikari as h
import pytest

from dd.hmessage.embed import MultiImageEmbedList

_URL = "https://example.com"


def test_zero_images_yields_one_imageless_embed():
    embeds = MultiImageEmbedList(_URL)
    assert len(embeds) == 1
    assert embeds[0].image is None


def test_single_image_yields_one_embed():
    embeds = MultiImageEmbedList(_URL, images=["a.png"])
    assert len(embeds) == 1
    assert embeds[0].image is not None


def test_multiple_images_split_into_multiple_embeds():
    embeds = MultiImageEmbedList(_URL, images=["a.png", "b.png", "c.png"])
    assert len(embeds) == 3
    assert all(e.image is not None for e in embeds)


def test_image_kwarg_is_rejected():
    with pytest.raises(ValueError):
        MultiImageEmbedList(_URL, image="a.png")


def test_add_image_is_fluent():
    embeds = MultiImageEmbedList(_URL)
    assert embeds.add_image("a.png") is embeds


def test_from_embed_raises_without_url_or_default():
    # Regression: the designator query param used to make the post-construction
    # url check always pass, silently producing an embed with an invalid relative
    # url (`?multi_image_embed_designator=0`). The guard now validates the
    # resolved url *before* the query is appended, so this raises as intended.
    with pytest.raises(ValueError):
        MultiImageEmbedList.from_embed(h.Embed(title="t"))


def test_from_embed_uses_embed_url_when_present():
    src = h.Embed(title="t", url="https://example.com/page")
    embeds = MultiImageEmbedList.from_embed(src)
    assert str(embeds[0].url).startswith("https://example.com/page")


def test_from_embed_copies_metadata_with_default_url():
    src = h.Embed(title="Title", description="Desc")
    embeds = MultiImageEmbedList.from_embed(src, default_url=_URL)
    assert embeds[0].title == "Title"
    assert embeds[0].description == "Desc"
    assert embeds[0].url
