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

"""``materialize()`` draws the same thing ``serialize()`` writes.

The shared renderer has two back ends. ``serialize()`` produces an HTML string and is
asserted exhaustively by the golden corpus under ``node --test``. ``materialize()``
produces real DOM — and it is the one **production actually uses** for the mirror log
and the hybrid-post form previews (``mirror_log.js``, ``shared.js``), including the
mirror log's untrusted third-party content.

Nothing covered it. The builder's browser tests drive the *canvas*, which goes through
``serialize()``, so the DOM back end was the only part of the renderer with no test at
all — exactly the half where the escaping guarantees are load-bearing.

This runs the whole corpus through both and compares the results *as documents*, which
takes a little care — three ways the DOM path writes the same page differently:

- entities: a browser re-serialises ``&#x27;`` as ``'``,
- text nodes: ``materialize`` appends a button's emoji and its label as two adjacent
  text nodes, where parsing the equivalent HTML yields one,
- and ``style``: assigning ``el.style.borderLeftColor = "#ec42a5"`` goes through CSSOM,
  which canonicalises it to ``rgb(236, 66, 165)``.

None of those is a difference in the rendered page. Parsing the frozen string into a
DOM, canonicalising its ``style`` the same way, and comparing the re-serialised markup
absorbs all three while still catching a real divergence in structure, text or
attributes.
"""

import json
import os
import pathlib
import typing as t

import pytest

playwright_api = pytest.importorskip(
    "playwright.sync_api", reason="playwright is a dev dependency"
)

pytestmark = pytest.mark.browser

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent.parent / "preview_fixtures"
# Reuses the builder harness purely as a page that has cv2_model.js and cv2_render.js
# loaded; none of the builder itself is involved.
HARNESS = (
    pathlib.Path(__file__).resolve().parent.parent
    / "web_static/tests/builder_harness.html"
)

#: Matches NOW_MS in the JS corpus test — `<t:…:R>` is relative to it.
NOW_MS = 1785430800 * 1000

#: Build a spec the way each render mode's production caller does, materialize it, and
#: compare the re-serialised markup against the frozen expectation parsed into a DOM.
#: Returns "" on a match, or the drawn markup for the failure message.
_COMPARE = """
([kase, nowMs]) => {
  const R = window.CV2Render;
  const opts = { emoji: kase.emoji || {}, now: nowMs };
  let spec;
  switch (kase.render) {
    case "markdown":   spec = { tag: "div", md: kase.content }; break;
    case "snapshot":   spec = R.snapshotSpec(kase.payload, kase.kind); break;
    case "authored":   spec = R.nodesSpec(kase.sanitized || []); break;
    case "post_spec":  spec = R.nodesSpec(kase.nodes || []); break;
    case "diff":       spec = R.diffSpec(kase.annotated); break;
    default: return "unknown render mode: " + kase.render;
  }

  // Round-trip every style through CSSOM so both sides spell the accent the same way.
  // Clearing first is required: assigning a property its existing value is a no-op, and
  // the attribute keeps whatever text it was authored with. border-left-color is the
  // only style the renderer ever writes.
  const canonical = (root) => {
    root.querySelectorAll("[style]").forEach((el) => {
      const v = el.style.borderLeftColor;
      el.style.borderLeftColor = "";
      el.style.borderLeftColor = v;
    });
    return root.innerHTML;
  };

  const drawn = document.createElement("div");
  drawn.appendChild(R.materialize(spec, opts));

  const expected = document.createElement("div");
  // The markdown mode's expectation is a bare fragment, so wrap both sides alike.
  expected.innerHTML =
    kase.render === "markdown"
      ? "<div>" + kase.expected_html + "</div>"
      : kase.expected_html;

  const a = canonical(drawn);
  return a === canonical(expected) ? "" : a;
}
"""


def _cases() -> list[tuple[str, dict[str, t.Any]]]:
    out = []
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for case in data["cases"]:
            case.setdefault("emoji", data.get("emoji") or {})
            out.append((f"{path.stem}:{case['name']}", case))
    return out


@pytest.fixture
def page() -> t.Iterator[t.Any]:
    """One page with the renderer loaded, per module.

    Deliberately NOT session-scoped — see the same warning in ``test_builder_drag.py``:
    holding ``sync_playwright()`` open across the session breaks pytest-asyncio for
    every async test that follows.
    """
    with playwright_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
            )
        except playwright_api.Error as exc:
            pytest.skip(
                "no Chromium available — run "
                f"`uv run playwright install chromium` ({exc})"
            )
        # UTC, to match the corpus. `<t:…>` renders in the VIEWER'S zone now, so a page
        # left on the host's zone fails this suite everywhere but a UTC machine — which
        # CI is, so CI would never have caught it. The JS twin pins the same thing with
        # `process.env.TZ` (tests/preview_fixtures.test.js).
        pg = browser.new_page(timezone_id="UTC")
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(HARNESS.as_uri())
        pg.wait_for_function("() => !!window.CV2Render")
        yield pg
        browser.close()
        assert not errors, f"the renderer raised while under test: {errors}"


def test_the_dom_back_end_matches_the_frozen_corpus(page: t.Any) -> None:
    """Every corpus case, through ``materialize()``, in a real browser."""
    mismatches = []
    for name, case in _cases():
        drawn = page.evaluate(_COMPARE, [case, NOW_MS])
        if drawn:
            mismatches.append(
                f"{name}\n  drawn:    {drawn}\n  expected: {case['expected_html']}"
            )
    assert not mismatches, "materialize() drew something else:\n" + "\n".join(
        mismatches[:5]
    )


def test_the_corpus_was_actually_loaded() -> None:
    """A wrong fixture path would make the loop above iterate nothing and pass."""
    assert len(_cases()) > 90
