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

Playwright is a dev dependency, but the *browser* is a separate ~150MB download that
``uv sync`` does not fetch, so these **skip** rather than fail on a machine that has not
run it::

    uv run playwright install chromium
    make test-browser

CI installs the browser and runs them on every push — a browser test that only ever runs
on one developer's box rots exactly as fast as one nobody wrote.

Where a Chromium is already provisioned and ``playwright install`` cannot reach the
download host — a sandboxed runner, or a container that bakes the browser in — point at
it instead::

    PLAYWRIGHT_CHROMIUM_EXECUTABLE=/path/to/chrome make test-browser
"""

import os
import typing as t
from pathlib import Path

import pytest

playwright_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is not installed (it is in the `dev` dependency group)",
)

pytestmark = pytest.mark.browser

HARNESS = (
    Path(__file__).resolve().parent.parent / "web_static/tests/builder_harness.html"
)
HARNESS_URL = HARNESS.as_uri()

# The harness tree, in the DOM order .cv2b-blk appears in. Indices into this are what
# the tests grab, so a change to the fixture breaks them loudly rather than silently.
BLOCK_HEADING = 1  # the "# Weekly Reset" text INSIDE the container
# A separator, used wherever a test needs to SELECT a block without entering an
# editor: clicking a text block focuses its contenteditable, and the keyboard
# handler then treats Delete as typing rather than as a block deletion.
BLOCK_SEPARATOR = 2


@pytest.fixture
def page() -> t.Iterator[t.Any]:
    """A mounted harness, with any page error escalated to a test failure."""
    with playwright_api.sync_playwright() as p:
        try:
            # Falls back to Playwright's own download when unset (the normal case).
            browser = p.chromium.launch(
                executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
            )
        except playwright_api.Error as exc:
            # The package is installed but the browser binary is not — a plain `uv sync`
            # gets you here. Skip with the fix rather than failing the whole suite for
            # someone who never asked to run browser tests.
            pytest.skip(
                "no Chromium available — run "
                f"`uv run playwright install chromium` ({exc})"
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
    scope = armed["scope"].strip("[]")
    landed = f"[{scope + ',' if scope else ''}{armed['index']}]"

    page.mouse.up()
    # The ghost flies to the landing site before the mutation applies, so wait for the
    # OUTCOME rather than a fixed delay — how long that animation takes is not this
    # test's business, and hardcoding it here would make retuning the motion a test
    # failure.
    try:
        page.wait_for_function(
            "path => Array.from(document.querySelectorAll('.cv2b-blk'))"
            ".some(e => e.dataset.path === path)",
            arg=landed,
            timeout=3000,
        )
    except playwright_api.TimeoutError:  # pragma: no cover - failure path
        pytest.fail(
            f"armed {armed} but no block ever appeared at {landed}: {_paths(page)}"
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


def test_the_landing_mark_does_not_survive_the_next_paint(page):
    """`.cv2b-landed` must be set for exactly one paint.

    The canvas is rebuilt as innerHTML on every render, so a mark left on the state
    would re-apply the animation to a freshly created element on every subsequent paint
    — meaning every keystroke would replay it. commit() sets it, renders, then clears
    it; this is the assertion that keeps that true.
    """
    page.locator(".cv2b-blk").nth(BLOCK_SEPARATOR).click()
    page.keyboard.press("Delete")
    page.wait_for_function(
        "() => document.querySelectorAll('.cv2b-blk.cv2b-landed').length > 0",
        timeout=3000,
    )

    # Any later repaint that is not a mutation must come back clean. Selecting another
    # block is the cheapest one; the selector is kind-based so it survives the reshuffle
    # the delete just caused.
    page.locator('.cv2b-blk[data-kind="link_button"]').first.click()
    page.wait_for_timeout(150)
    assert page.locator(".cv2b-blk.cv2b-landed").count() == 0, (
        "the landing mark persisted, so the animation replays on every later paint"
    )


def test_a_deleted_block_is_really_gone_after_its_collapse(page):
    """The exit animation runs BEFORE the mutation, so a bug there swallows the delete.

    commitAfterCollapse commits on the animation's cancel path too, precisely so an
    interrupted collapse cannot leave the block on screen and the change unapplied.
    """
    before = _paths(page)
    page.locator(".cv2b-blk").nth(BLOCK_SEPARATOR).click()
    page.keyboard.press("Delete")
    page.wait_for_function(
        "n => document.querySelectorAll('.cv2b-blk').length < n",
        arg=len(before),
        timeout=3000,
    )
    assert len(_paths(page)) < len(before)
