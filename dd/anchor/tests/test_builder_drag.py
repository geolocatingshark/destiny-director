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

"""Browser tests for the CV2 builder's drag layer.

``plans/web_cv2_builder_followups.md`` §6 records the DOM layer as the one part of the
builder with no automated coverage, and says the work left is *choosing assertions that
will not rot* — not building a fixture. So this file asserts **behavioural invariants**
only: where a block lands, and whether a release commits at all. Nothing here looks at
appearance, and nothing takes a screenshot; restyling the builder must never fail these.

The fixture is ``dd/anchor/web_static/tests/builder_harness.html``, which mounts the
widget over ``file://`` with a fixed tree. ``initCv2Builder`` takes its seed nodes and
all three server round-trips as injected options, so no server, auth or database is
involved.

**Not wired into the default suite yet.** Playwright is deliberately not a project
dependency — that is an open call about what the dev container should carry. Until it is
made, these skip. To run them::

    uv add --dev playwright && uv run playwright install chromium
    uv run python -m pytest dd/anchor/tests/test_builder_drag.py

Where a Chromium is already provisioned and ``playwright install`` cannot reach the
download host — a sandboxed CI runner, or a container that bakes the browser in — point
at it instead::

    PLAYWRIGHT_CHROMIUM_EXECUTABLE=/opt/pw-browsers/chromium-1194/chrome-linux/chrome \\
        uv run python -m pytest dd/anchor/tests/test_builder_drag.py

If the answer turns out to be no, deleting this file costs nothing: it shares no code
with the rest of the suite.
"""

import os
import typing as t
from pathlib import Path

import pytest

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is not a project dependency yet; see this module's docstring",
)

HARNESS = (
    Path(__file__).resolve().parent.parent / "web_static/tests/builder_harness.html"
)
HARNESS_URL = HARNESS.as_uri()

# The harness tree, in the DOM order .cv2b-blk appears in. Indices into this are what
# the tests grab, so a change to the fixture breaks them loudly rather than silently.
BLOCK_HEADING = 1  # the "# Weekly Reset" text INSIDE the container


@pytest.fixture
def page() -> t.Iterator[t.Any]:
    """A mounted harness, with any page error escalated to a test failure."""
    with playwright_api.sync_playwright() as p:
        # Falls back to Playwright's own download when unset (the normal case).
        browser = p.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
        )
        pg = browser.new_page(viewport={"width": 1280, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(HARNESS_URL)
        pg.wait_for_selector(".cv2b-blk")
        yield pg
        browser.close()
        assert not errors, f"the builder raised while under test: {errors}"


def _paths(pg) -> list[str]:
    """Every block's data-path, in DOM order — the tree's shape as the page sees it."""
    return pg.eval_on_selector_all(".cv2b-blk", "els => els.map(e => e.dataset.path)")


def _armed(pg) -> dict | None:
    return pg.evaluate(
        """() => {
             const a = document.querySelector('.cv2b-rail.cv2b-armed');
             return a ? {scope: a.dataset.scope, index: a.dataset.index} : null;
           }"""
    )


def _grab(pg, block_index: int):
    """Press the pointer on a block's drag grip, which is display:none until hover."""
    blk = pg.locator(".cv2b-blk").nth(block_index)
    blk.hover()
    grip = blk.locator("> .cv2b-grip").first
    box = grip.bounding_box()
    assert box, "the grip did not become visible on hover"
    pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    pg.mouse.down()


def test_a_block_lands_on_the_rail_that_was_armed(page):
    """The highlight and the landing site must be the same answer.

    endDrag used to run its own hit test on pointerup, independently of the one that
    drew the highlight. They agreed only by coincidence — autoscroll re-arms against
    freshly scrolled content, and the nearest search deliberately holds a target through
    small movements. When they disagree, a block lands somewhere other than the rail the
    author is looking at.
    """
    last_rail = page.locator(".cv2b-rail").last.bounding_box()
    _grab(page, BLOCK_HEADING)
    # Aim below the last rail and clear of it, so the exact hit test misses entirely and
    # the nearest-target search is what decides.
    page.mouse.move(last_rail["x"] + 120, last_rail["y"] + 34, steps=12)
    page.wait_for_timeout(60)

    armed = _armed(page)
    assert armed, "an off-rail pointer inside the cap should still arm a target"
    page.mouse.up()
    page.wait_for_timeout(120)

    scope = armed["scope"].strip("[]")
    landed = f"[{scope + ',' if scope else ''}{armed['index']}]"
    assert landed in _paths(page), (
        f"armed {armed} but no block exists at {landed} afterwards"
    )


def test_a_release_far_from_every_rail_cancels(page):
    """Releasing over the palette or the inspector stays an escape hatch.

    The nearest search has a distance cap precisely so this keeps working: without it a
    drag begun by accident would always commit somewhere.
    """
    before = _paths(page)
    _grab(page, BLOCK_HEADING)
    page.mouse.move(60, 700, steps=12)  # far left, over the palette
    page.wait_for_timeout(60)
    assert _armed(page) is None, "a far-away pointer must not arm anything"
    page.mouse.up()
    page.wait_for_timeout(120)
    assert _paths(page) == before, "a cancelled drag must leave the tree alone"


def test_a_drop_between_two_rails_still_finds_one(page):
    """A rail is 0.62rem tall. Landing exactly on one with a fingertip is a coin flip,
    so a release that falls between two used to spring back with no explanation."""
    rails = page.eval_on_selector_all(
        ".cv2b-rail:not(.cv2b-blocked)",
        "els => els.map(e => { const b = e.getBoundingClientRect();"
        " return {top: b.top, bottom: b.bottom, left: b.left, right: b.right}; })",
    )
    assert len(rails) >= 3, "the fixture should offer several legal rails"
    gap_y = (rails[1]["bottom"] + rails[2]["top"]) / 2
    assert rails[2]["top"] > rails[1]["bottom"], "expected a gap between these rails"

    _grab(page, BLOCK_HEADING)
    page.mouse.move(rails[1]["left"] + 40, gap_y, steps=10)
    page.wait_for_timeout(60)
    assert _armed(page) is not None, "a between-rails drop should snap to the nearer"
    page.mouse.up()
