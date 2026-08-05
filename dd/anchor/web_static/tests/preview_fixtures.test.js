// Copyright © 2019-present gsfernandes81
//
// This file is part of "dd" henceforth referred to as "destiny-director".
// Licensed under the GNU AGPL v3 or later; see the project LICENSE.

// The JS half of the shared golden corpus (dd/anchor/preview_fixtures).
//
// The Python half is dd/anchor/tests/test_preview_fixtures.py, and it asserts the SAME
// `expected_html` strings from the SAME files. That is the entire point: while the
// renderer is being moved from Python to JavaScript
// (plans/preview_renderer_unification.md), a corpus that only one side reads proves
// nothing. Two implementations held to one byte-exact expectation is what makes the
// port checkable instead of hopeful.
//
// Each case names the surface it stands for in its `render` field:
//
//   markdown  the leaf layer — cv2_model.renderMd
//
//   snapshot  the node walker — cv2_render.snapshotSpec + serialize
//
//   authored  the builder's publish confirmation — the server sanitizes, the client
//             renders. sanitize_for_preview is a send-safety transform with no client
//             mirror and is not getting one, so the fixture carries its output and this
//             asserts the render from there, which is exactly the split in production.
//
//   post_spec the weekly-reset / trials / rotation previews — the server sends the
//             post's own node tree (post_spec_nodes) and the client draws it.
//
//   diff      the mirror log's version diff. The alignment stays in Python (it needs
//             difflib), so the fixture carries the annotated tree it produces and this
//             asserts the render from there — against the HTML the OLD Python diff
//             renderer emitted, which is what makes the port checkable.

// `<t:…>` tokens render in the VIEWER'S timezone now, so a test asserting one is only
// reproducible with the zone pinned. Set before requiring anything that reads a clock —
// Node picks TZ up on assignment, so this holds wherever the suite runs.
process.env.TZ = "UTC";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const M = require("../cv2_model.js");
const R = require("../cv2_render.js");

const FIXTURE_DIR = path.join(__dirname, "..", "..", "preview_fixtures");

// The corpus' frozen clock, matching NOW in the Python test module
// (2026-07-30T17:00:00Z). Python takes a datetime; renderMd takes epoch milliseconds.
const NOW_UNIX = 1785430800;
const NOW_MS = NOW_UNIX * 1000;

function load(name) {
  const raw = fs.readFileSync(path.join(FIXTURE_DIR, name), "utf8");
  return JSON.parse(raw);
}

// There is one renderer, and it is this one — so regenerating the corpus' expectations
// is this side's job. Run the Python half FIRST (it writes the data fields rendered
// from), then:
//
//   UPDATE_PREVIEW_FIXTURES=1 node --test dd/anchor/web_static/tests/preview_fixtures.test.js
//
// Read the diff. Regenerate only when you mean to change what a preview looks like.
const UPDATE = !!process.env.UPDATE_PREVIEW_FIXTURES;

/** Draw one case the way its `render` mode says to. */
function renderCase(c, fileEmoji) {
  const opts = { emoji: c.emoji || fileEmoji || {}, now: NOW_MS };
  switch (c.render) {
    case "markdown":
      return M.renderMd(c.content, opts.emoji, opts.now);
    case "snapshot":
      return R.serialize(R.snapshotSpec(c.payload, c.kind), opts);
    case "authored":
      return R.serialize(R.nodesSpec(c.sanitized || []), opts);
    case "post_spec":
      return R.serialize(R.nodesSpec(c.nodes || []), opts);
    case "diff":
      return R.serialize(R.diffSpec(c.annotated), opts);
    default:
      throw new Error(`unknown render mode ${c.render} in ${c.name}`);
  }
}

if (UPDATE) {
  for (const name of fs.readdirSync(FIXTURE_DIR)) {
    if (!name.endsWith(".json")) continue;
    const data = load(name);
    for (const c of data.cases) c.expected_html = renderCase(c, data.emoji);
    fs.writeFileSync(
      path.join(FIXTURE_DIR, name),
      JSON.stringify(data, null, 2) + "\n",
      "utf8",
    );
  }
}

test("the fixture directory is where both sides think it is", () => {
  // A silently-wrong path would make the loop below iterate zero cases and pass, so the
  // corpus has to prove it was actually read.
  assert.ok(fs.existsSync(FIXTURE_DIR), `missing fixture dir: ${FIXTURE_DIR}`);
  assert.ok(load("markdown.json").cases.length > 0, "markdown.json has no cases");
});

// Every case in the corpus, through the same dispatch the regeneration above uses. One
// loop rather than one per render mode: five near-identical loops had already drifted
// (only some of them honoured a case's own `emoji` override), and a new mode would have
// needed a sixth copy to be covered at all.
const seen = {};

for (const file of fs.readdirSync(FIXTURE_DIR).sort()) {
  if (!file.endsWith(".json")) continue;
  const data = load(file);
  const group = path.parse(file).name;
  for (const c of data.cases) {
    seen[c.render] = (seen[c.render] || 0) + 1;
    test(`${group}:${c.name} renders to its frozen html`, () => {
      assert.equal(
        typeof c.expected_html,
        "string",
        `${c.name} has no expected_html — regenerate with UPDATE_PREVIEW_FIXTURES=1`,
      );
      assert.equal(
        renderCase(c, data.emoji),
        c.expected_html,
        `${c.name} diverged from the frozen expectation`,
      );
    });
  }
}

test("the corpus covered every render mode, in the depth it is meant to", () => {
  // The loop above is driven by each case's `render` field. A typo there would produce a
  // test that throws (renderCase has no default arm) — but a mode losing all its cases,
  // or the snapshot corpus quietly shrinking, would not. Assert the shape outright.
  assert.deepEqual(
    Object.keys(seen).sort(),
    ["authored", "diff", "markdown", "post_spec", "snapshot"],
    `render modes present: ${JSON.stringify(seen)}`,
  );
  assert.ok(
    seen.snapshot > 30,
    `only ${seen.snapshot} snapshot cases ran — check the fixtures' render fields`,
  );
});
