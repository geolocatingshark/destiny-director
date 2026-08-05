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

"""Tests for the shared post core (reset maths, PostSpec, post_spec_nodes).

The rendering these used to assert moved to the shared golden corpus
(``dd/anchor/preview_fixtures``), which holds the Python and JavaScript renderers
to one output rather than checking either alone."""

import dataclasses
import datetime as dt
import typing as t

import pytest

from dd.anchor import hybrid_post_core as hpc


def test_hybrid_post_spec_has_no_autopost_hooks() -> None:
    # weekly_reset/trials no longer carry a reset-day autopost toggle, so the shared
    # spec dropped its get_autopost/set_autopost hooks entirely.
    fields = {f.name for f in dataclasses.fields(hpc.HybridPostSpec)}
    assert "get_autopost" not in fields
    assert "set_autopost" not in fields


def test_core_has_no_auto_route_handler() -> None:
    # The POST /{prefix}/auto handler that wrote the toggle is removed.
    assert not hasattr(hpc, "auto")


def test_autopostsettings_has_no_weekly_or_trials_toggle() -> None:
    from dd.common import schemas

    aps = schemas.AutoPostSettings
    for name in (
        "get_weekly_reset_enabled",
        "set_weekly_reset",
        "get_trials_enabled",
        "set_trials",
    ):
        assert not hasattr(aps, name), name
    # The generic accessors and other feeds' toggles are untouched.
    assert hasattr(aps, "get_enabled") and hasattr(aps, "get_iron_banner_enabled")


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

    # Guides first, then the standard Support button.
    assert c.footer_button_specs(guides=[("Guide", "https://g.example")]) == [
        ("Guide", "https://g.example"),
        ("Support Us", c.KOFI_URL),
    ]
    # No guides -> just the Support button (e.g. Portal Ops / Weekly Reset).
    assert c.footer_button_specs() == [("Support Us", c.KOFI_URL)]
    # A row caps at 5 buttons, so at most 4 guides.
    with pytest.raises(ValueError):
        c.footer_button_specs(guides=[("a", "https://x")] * 5)






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




# --- post_spec_nodes ------------------------------------------------------------------


def _drop_defaults(value: t.Any) -> t.Any:
    """Strip hikari's explicit defaults so two descriptions of a post compare.

    ``build_cv2`` goes through hikari's builders, which spell out ``spoiler: false`` and
    ``disabled: false`` and hand back URL objects; ``post_spec_nodes`` writes the raw
    JSON Discord actually needs. Neither difference is a difference in the post.
    """
    defaults = {"spoiler": False, "disabled": False}
    if isinstance(value, dict):
        return {
            k: _drop_defaults(v)
            for k, v in value.items()
            if not (k in defaults and v == defaults[k])
        }
    if isinstance(value, list):
        return [_drop_defaults(v) for v in value]
    if isinstance(value, str):
        return value
    return str(value) if type(value).__name__ == "URL" else value


@pytest.mark.parametrize(
    "body,image,buttons",
    [
        ("just a body", None, ()),
        ("with an image", "https://example.com/i.png", ()),
        ("with buttons", None, (("Guide", "https://example.com/g"),)),
        (
            "everything",
            "https://example.com/i.png",
            (("Guide", "https://example.com/g"), ("Support", "https://example.com/s")),
        ),
    ],
)
def test_post_spec_nodes_matches_build_cv2(
    body: str, image: str | None, buttons: tuple
) -> None:
    """The preview's tree IS the post's tree.

    This is the pin that makes retiring the old ``.post-*`` previewer safe: rather than
    a second markup vocabulary approximating the post, the previewer renders the very
    node list ``build_cv2`` sends. If the two ever diverge, the preview stops being a
    preview — so compare them directly, on every shape a producer emits.
    """
    live, _ = hpc.build_cv2(body, image, buttons=buttons).components[0].build()
    spec = hpc.PostSpec.cv2(body, image, buttons=buttons)

    assert _drop_defaults(live) == _drop_defaults(hpc.post_spec_nodes(spec)[0])


def test_post_spec_nodes_drops_a_non_http_image() -> None:
    # Matching the renderer, which refuses a non-http(s) media URL — better an absent
    # image in the preview than one the post will not carry.
    spec = hpc.PostSpec.cv2("body", "ftp://example.com/i.png")
    kinds = [c["type"] for c in hpc.post_spec_nodes(spec)[0]["components"]]
    assert kinds == [10]
