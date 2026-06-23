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

# Pure data-transformation tests for HMessage — no Discord I/O.

import hikari as h
import pytest

from dd.hmessage.message import HMessage, HMessageEmbed


def _embed_with_image(title: str = "t", description: str = "d") -> h.Embed:
    # HMessageEmbed.__eq__ dereferences ``_image.url`` unconditionally, so only
    # compare embeds that actually carry an image.
    embed = h.Embed(title=title, description=description)
    embed.set_image("https://example.com/i.png")
    return embed


# --- validators ----------------------------------------------------------------


def test_content_over_2000_chars_raises():
    with pytest.raises(ValueError):
        HMessage(content="x" * 2001)


def test_more_than_ten_embeds_raises():
    with pytest.raises(ValueError):
        HMessage(embeds=[h.Embed() for _ in range(11)])


def test_more_than_ten_attachments_raises():
    with pytest.raises(ValueError):
        HMessage(attachments=["a"] * 11)


# --- to_message_kwargs ---------------------------------------------------------


def test_to_message_kwargs_round_trips_fields():
    embed = h.Embed(description="body")
    msg = HMessage(content="hi", embeds=[embed])
    kwargs = msg.to_message_kwargs()
    assert kwargs == {"content": "hi", "embeds": [embed], "attachments": []}


# --- __add__ -------------------------------------------------------------------


def test_add_joins_content_with_newline():
    assert (HMessage(content="a") + HMessage(content="b")).content == "a\nb"


def test_add_does_not_double_newline_when_already_present():
    assert (HMessage(content="a\n") + HMessage(content="b")).content == "a\nb"


def test_add_combines_embeds_and_attachments():
    merged = HMessage(embeds=[h.Embed()], attachments=["x"]) + HMessage(
        embeds=[h.Embed()], attachments=["y"]
    )
    assert len(merged.embeds) == 2
    assert merged.attachments == ["x", "y"]


def test_add_rejects_non_hmessage():
    with pytest.raises(TypeError):
        _ = HMessage() + object()


# --- merge_content_into_embed --------------------------------------------------


def test_merge_content_creates_embed_when_none():
    msg = HMessage(content="hello")
    msg.merge_content_into_embed()
    assert msg.content == ""
    assert len(msg.embeds) == 1
    assert msg.embeds[0].description == "hello"


def test_merge_content_prepends_into_existing_embed():
    msg = HMessage(content="hi", embeds=[h.Embed(description="body")])
    msg.merge_content_into_embed(prepend=True)
    assert msg.content == ""
    assert msg.embeds[0].description == "hi\n\nbody"


# --- merge_url_as_image_into_embed (default_url safety net) ---------------------


def test_merge_url_falls_back_to_default_url_when_embed_has_none():
    # Regression: a synthesised embed has no url. Without the default_url safety
    # net this produced an invalid relative url; now it anchors on default_url.
    msg = HMessage(content="")
    msg.merge_url_as_image_into_embed(
        "https://example.com/i.png", 0, default_url="https://example.com/canonical"
    )
    assert str(msg.embeds[0].url).startswith("https://example.com/canonical")


def test_merge_url_raises_when_no_url_available():
    # With neither an embed url nor a default_url, the (now live) guard fires
    # instead of silently emitting an invalid embed url.
    msg = HMessage(content="")
    with pytest.raises(ValueError):
        msg.merge_url_as_image_into_embed("https://example.com/i.png", 0)


# --- remove_all_embed_thumbnails -----------------------------------------------


def test_remove_all_embed_thumbnails():
    embed = h.Embed()
    embed.set_thumbnail("https://example.com/t.png")
    msg = HMessage(embeds=[embed])
    msg.remove_all_embed_thumbnails()
    assert msg.embeds[0].thumbnail is None


# --- HMessageEmbed equality ----------------------------------------------------


def test_hmessage_embed_equals_matching_hikari_embed():
    a = HMessageEmbed.from_embed(_embed_with_image("t", "d"))
    assert a == _embed_with_image("t", "d")


def test_hmessage_embed_differs_on_title():
    a = HMessageEmbed.from_embed(_embed_with_image("t", "d"))
    assert a != _embed_with_image("other", "d")


def test_hmessage_embed_not_equal_to_non_embed():
    a = HMessageEmbed.from_embed(_embed_with_image())
    assert a != "not an embed"
