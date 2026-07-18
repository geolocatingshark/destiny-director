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

"""Tests for the shared post-preview core (PostSpec + render_post_spec)."""

import typing as t

import hikari as h
import pytest

from dd.anchor import hybrid_post_core as hpc


class _StubEmoji:
    """A stand-in for a guild ``KnownCustomEmoji`` — only ``.url`` is read."""

    def __init__(self, url: str) -> None:
        self.url = url


def test_postspec_cv2_factory_and_from_payload() -> None:
    direct = hpc.PostSpec.cv2("# Hi", "https://ex.com/a.png")
    assert direct.kind == "cv2"
    assert direct.body == "# Hi" and direct.image_url == "https://ex.com/a.png"

    # from_payload defaults to cv2 and coerces a blank/missing image to None.
    parsed = hpc.PostSpec.from_payload({"body": "# Hi", "image_url": ""})
    assert parsed == hpc.PostSpec.cv2("# Hi", None)
    assert hpc.PostSpec.from_payload({}) == hpc.PostSpec.cv2("", None)
    assert hpc.PostSpec.from_payload({"kind": "cv2", "body": "x"}).body == "x"


def test_postspec_from_payload_rejects_unknown_kind() -> None:
    # The embed kind (and any other) isn't renderable yet — surfaced as ValueError so a
    # route can 422 it.
    with pytest.raises(ValueError, match="Unsupported post kind"):
        hpc.PostSpec.from_payload({"kind": "embed", "title": "x"})


def test_render_post_spec_cv2_matches_render_post_html() -> None:
    emoji = {"Bungie": _StubEmoji("https://cdn.discordapp.com/emojis/1.png")}
    emoji_d = t.cast("dict[str, h.Emoji]", emoji)
    body = "# Title\n:Bungie: hi"
    spec = hpc.PostSpec.cv2(body, "https://ex.com/a.png")
    # The cv2 branch is exactly the existing string renderer over body + image.
    assert hpc.render_post_spec(spec, emoji_d) == hpc.render_post_html(
        body, emoji_d, "https://ex.com/a.png"
    )
    out = hpc.render_post_spec(spec, emoji_d)
    assert '<span class="md-h1">Title</span>' in out
    assert '<img class="emoji"' in out
    assert '<img class="post-image" src="https://ex.com/a.png"' in out


def test_render_post_spec_renders_h2_heading() -> None:
    # '## ' is an H2 heading (used by the Lost Sector post); rendered as an md-h2 span
    # with the inline link, not left as literal '## ' text.
    spec = hpc.PostSpec.cv2("## [World Lost Sectors](https://kyber3000.com/LS)")
    out = hpc.render_post_spec(spec, t.cast("dict[str, h.Emoji]", {}))
    assert (
        '<span class="md-h2"><a href="https://kyber3000.com/LS">World Lost Sectors</a>'
        in out
    )
    assert "## " not in out


def test_render_post_spec_embed_kind_not_yet_supported() -> None:
    # Reserved for the user-commands work; must not silently render as empty.
    spec = hpc.PostSpec(kind="embed", body="")
    with pytest.raises(ValueError, match="Cannot render post kind"):
        hpc.render_post_spec(spec, t.cast("dict[str, h.Emoji]", {}))
