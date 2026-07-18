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

import datetime as dt
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


def test_normalize_heading_spacing_matches_discord() -> None:
    # Discord gives ##/### sub-headings a gap ABOVE and tight content below, but bodies
    # author the blank AFTER the heading. Normalise to one blank before, none after — so
    # the pre-wrap preview reads like the posted message. The # (H1) title keeps its own
    # trailing blank.
    body = [
        "# Title",
        "",
        "Live until X",
        "### Game Modes",
        "",
        "- Control",
        "### Bonus Focus Pool",
        "",
        "weapon",
    ]
    assert hpc._normalize_heading_spacing(body) == [
        "# Title",
        "",  # H1 title keeps its body-authored gap below
        "Live until X",
        "",  # inserted gap ABOVE the sub-heading
        "### Game Modes",
        "- Control",  # tight below the heading (blank dropped)
        "",  # inserted gap above the next sub-heading
        "### Bonus Focus Pool",
        "weapon",
    ]


def test_normalize_heading_spacing_leaves_leading_heading_and_collapses() -> None:
    # A sub-heading at the very top gets no blank inserted above it; multiple
    # body-authored blanks above a heading collapse to exactly one.
    assert hpc._normalize_heading_spacing(["### Top", "x"]) == ["### Top", "x"]
    assert hpc._normalize_heading_spacing(["a", "", "", "### H", "b"]) == [
        "a",
        "",
        "### H",
        "b",
    ]


def test_footer_button_specs() -> None:
    from dd.common import components as c

    # Guides first, then the standard Support + Kyber's Corner buttons.
    assert c.footer_button_specs(guides=[("Guide", "https://g.example")]) == [
        ("Guide", "https://g.example"),
        ("Support Us", c.KOFI_URL),
        ("Kyber's Corner", c.KYBERS_CORNER_URL),
    ]
    # No guides -> just the two shared buttons (e.g. Portal Ops / Weekly Reset).
    assert c.footer_button_specs() == [
        ("Support Us", c.KOFI_URL),
        ("Kyber's Corner", c.KYBERS_CORNER_URL),
    ]
    # A row caps at 5 buttons, so at most 3 guides.
    with pytest.raises(ValueError):
        c.footer_button_specs(guides=[("a", "https://x")] * 4)


def test_render_post_html_renders_footer_buttons() -> None:
    # Footer link buttons render as a post-buttons div of styled <a>s, in order; a
    # non-http(s) url is dropped (never rendered as a link).
    out = hpc.render_post_html(
        "# Title",
        t.cast("dict[str, h.Emoji]", {}),
        None,
        buttons=[("Guide", "https://g.example"), ("Support Us", "https://ko-fi.com/x")],
    )
    assert '<div class="post-buttons">' in out
    assert (
        '<a class="post-button" href="https://g.example" '
        'target="_blank" rel="noopener noreferrer">Guide</a>' in out
    )
    assert ">Support Us</a>" in out
    dropped = hpc.render_post_html(
        "# Title",
        t.cast("dict[str, h.Emoji]", {}),
        None,
        buttons=[("Bad", "javascript:alert(1)")],
    )
    assert "post-button" not in dropped


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


def test_format_ts_relative_and_per_letter() -> None:
    now = dt.datetime(2026, 7, 14, 17, tzinfo=dt.UTC)
    base = 1784048400  # == now
    # ':R' relative countdown, from the render-time clock (largest whole unit).
    assert hpc._format_ts(base + 3 * 86400, "R", now=now) == "in 3 days"
    assert hpc._format_ts(base - 2 * 3600, "R", now=now) == "2 hours ago"
    assert hpc._format_ts(base + 86400, "R", now=now) == "in 1 day"  # singular
    # Other letters: time / date variants, all UTC-noted where relevant.
    assert hpc._format_ts(base, "t", now=now) == "5:00 PM (UTC)"
    assert hpc._format_ts(base, "T", now=now) == "5:00:00 PM (UTC)"
    assert hpc._format_ts(base, "d", now=now) == "07/14/2026"
    assert hpc._format_ts(base, "D", now=now) == "July 14, 2026"
    # 'f' (and anything else) keep the long-date short-time (unchanged behaviour).
    assert hpc._format_ts(base, "f", now=now) == "Jul 14, 2026 5:00 PM (UTC)"


def test_render_post_spec_relative_ts_is_not_literal() -> None:
    # A '<t:…:R>' countdown (used by legacy 'resets <t:…:R>') renders as a relative
    # phrase, never left as literal '<t:…:R>' text.
    spec = hpc.PostSpec.cv2("resets <t:9999999999:R>")
    out = hpc.render_post_spec(spec, t.cast("dict[str, h.Emoji]", {}))
    assert "<t:" not in out
    assert "in " in out or "ago" in out


def test_render_post_spec_embed_kind_not_yet_supported() -> None:
    # Reserved for the user-commands work; must not silently render as empty.
    spec = hpc.PostSpec(kind="embed", body="")
    with pytest.raises(ValueError, match="Cannot render post kind"):
        hpc.render_post_spec(spec, t.cast("dict[str, h.Emoji]", {}))
