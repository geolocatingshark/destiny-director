// Copyright © 2019-present gsfernandes81
//
// This file is part of "dd" henceforth referred to as "destiny-director".
// Licensed under the GNU AGPL v3 or later; see the project LICENSE.

// Cross-file CSS guards: which sheet owns what, and which sheet wins.
//
// There is no bundler, so nothing links a page's <script> to the stylesheet that script's
// output needs. That pairing is a convention, and conventions rot silently: a page that
// draws charts without charts.css renders real, correctly-shaped SVG in the wrong colours
// — nothing throws, nothing logs, and it looks plausible enough to miss.
//
// This is what replaced the copy: chart chrome used to be pasted into both stats.css and
// mirror_log.css (whose own comment admitted it "mirrors stats.css"), so the two had
// already drifted into different formattings of the same rules.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const STATIC_DIR = path.join(__dirname, "..");

const pages = () =>
  fs
    .readdirSync(STATIC_DIR)
    .filter((name) => name.endsWith(".html"))
    .map((name) => ({
      name,
      text: fs.readFileSync(path.join(STATIC_DIR, name), "utf8"),
    }));

test("a page that draws charts also loads the chart styles", () => {
  const missing = pages()
    .filter((p) => p.text.includes("/static/charts.js"))
    .filter((p) => !p.text.includes("/static/charts.css"))
    .map((p) => p.name);
  assert.deepEqual(
    missing,
    [],
    "these pages load charts.js without charts.css, so charts render unstyled",
  );
});

test("a page that styles a rendered post also loads the renderer", () => {
  // The mirror image of the charts rule. cv2_preview.css styles what cv2_render.js
  // emits; a page with the sheet and no renderer shows nothing, and a page with the
  // renderer and no sheet shows an unstyled pile of divs that still reads as a message
  // — plausible enough to ship by accident.
  const mismatched = pages()
    .filter(
      (p) =>
        p.text.includes("/static/cv2_preview.css") !==
        p.text.includes("/static/cv2_render.js"),
    )
    .map((p) => p.name);
  assert.deepEqual(
    mismatched,
    [],
    "cv2_preview.css and cv2_render.js must be loaded together",
  );
});

test("cv2_render.js is loaded after the model it consumes", () => {
  // Load order IS the dependency graph here — cv2_render.js reads window.CV2Model at
  // definition time, so a page that lists it first gets `undefined` and dies on the
  // first render with a message that points nowhere near the real mistake. Two shared
  // files were manageable by convention; four are not.
  const wrong = pages()
    .filter((p) => p.text.includes("/static/cv2_render.js"))
    .filter((p) => {
      const model = p.text.indexOf("/static/cv2_model.js");
      return model === -1 || model > p.text.indexOf("/static/cv2_render.js");
    })
    .map((p) => p.name);
  assert.deepEqual(
    wrong,
    [],
    "these pages load cv2_render.js without cv2_model.js before it",
  );
});

test("charts.css is not loaded by pages that draw no charts", () => {
  // The reverse direction, so the sheet does not quietly become a second shared.css.
  const pointless = pages()
    .filter((p) => p.text.includes("/static/charts.css"))
    .filter((p) => !p.text.includes("/static/charts.js"))
    .map((p) => p.name);
  assert.deepEqual(pointless, [], "these pages load charts.css but draw no charts");
});

test("chart chrome lives only in charts.css", () => {
  // The duplication this file exists to prevent: a page sheet re-styling what charts.js
  // emits. Page-level *placement* of a chart is fine (stats sets its own margin), so only
  // the classes charts.js actually draws are off limits.
  const OWNED = [
    "chart-svg",
    "chart-grid",
    "chart-tick",
    "chart-line",
    "chart-dot",
    "chart-bar",
    "chart-crosshair",
    "chart-overlay",
    "chart-empty",
    "chart-legend",
    "chart-tooltip",
    "legend-item",
    "legend-key",
    "spark-line",
    "sparkline",
  ];
  const sheets = fs
    .readdirSync(STATIC_DIR)
    .filter((n) => n.endsWith(".css") && n !== "charts.css");

  const offenders = [];
  for (const name of sheets) {
    const text = fs.readFileSync(path.join(STATIC_DIR, name), "utf8");
    for (const cls of OWNED) {
      if (new RegExp(`\\.${cls}\\b[^;{]*\\{`).test(text)) {
        offenders.push(`${name}: .${cls}`);
      }
    }
  }
  assert.deepEqual(offenders, [], "move these into charts.css rather than restating them");
});


// --- shared.css must not out-specify the pages it serves -----------------------------

test("the shared focus ring stays at element specificity", () => {
  // A class in this selector lifts it to (0,2,1), above the page overrides at (0,2,0)
  // that exist precisely to change it — stats' 1px search ring and its INSET -2px
  // segmented control. Adding `:not(.no-focus-ring)` here did exactly that, and the only
  // symptom was two rings quietly changing offset. The opt-out is its own rule instead.
  const css = fs
    .readFileSync(path.join(STATIC_DIR, "shared.css"), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, ""); // comments discuss selectors; only rules count
  const rule = css.match(/([^\n{}]*:focus-visible[^{]*)\{[^}]*outline:\s*2px/);
  assert.ok(rule, "shared.css should define one element-level focus ring");
  assert.doesNotMatch(
    rule[1],
    /\.[a-zA-Z]/,
    `the shared focus ring must not carry a class selector — found: ${rule[1].trim()}`,
  );
});

test("no page carries an executable inline script", () => {
  // This is the invariant `script-src 'self'` rests on (SECURITY_HEADERS in
  // dd/anchor/web.py). A `<script>` with no `src` and no non-executable `type` is
  // exactly what CSP blocks — and it would fail in production only, on a page a
  // developer had already tested locally without the header. Catch it here instead.
  //
  // `type="application/json"` is allowed: the three templated pages ship their
  // server-injected data that way, and CSP treats a non-executable type as data.
  const offenders = [];
  for (const page of pages()) {
    for (const [, attrs] of page.text.matchAll(/<script([^>]*)>/g)) {
      const hasSrc = /\ssrc=/.test(attrs);
      const isData = /\stype="application\/json"/.test(attrs);
      if (!hasSrc && !isData) offenders.push(`${page.name}: <script${attrs}>`);
    }
  }
  assert.deepEqual(
    offenders,
    [],
    "inline scripts are blocked by script-src 'self' — move them to /static/",
  );
});
