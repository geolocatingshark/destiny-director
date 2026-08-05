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
// Two of the four render modes are asserted here:
//
//   markdown  the leaf layer — cv2_model.renderMd
//   snapshot  the node walker — cv2_render.snapshotSpec + serialize
//
// The other two stay Python-only for now, on purpose:
//
//   authored  runs cv2_nodes.sanitize_for_preview first, which is a send-safety
//             transform with no client mirror and is not getting one — the server sends
//             an already-sanitized tree (plan phase 3).
//   diff      needs the annotation layer that does not exist yet (plan phase 6).
//   post_spec renders the .post-* vocabulary that plan phase 4 retires outright.

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

test("the fixture directory is where both sides think it is", () => {
  // A silently-wrong path would make every loop below iterate zero cases and pass, so
  // the corpus has to prove it was actually read.
  assert.ok(fs.existsSync(FIXTURE_DIR), `missing fixture dir: ${FIXTURE_DIR}`);
  assert.ok(load("markdown.json").cases.length > 0, "markdown.json has no cases");
});

const markdown = load("markdown.json");

for (const c of markdown.cases) {
  test(`markdown:${c.name} renders to its frozen html`, () => {
    assert.equal(
      M.renderMd(c.content, markdown.emoji, NOW_MS),
      c.expected_html,
      `markdown:${c.name} diverged from the Python renderer`,
    );
  });
}

test("every markdown case carries an expectation", () => {
  // Guards the loop above: a case with no `expected_html` would otherwise compare
  // against undefined only if renderMd also returned undefined — which it never does,
  // so it would fail loudly. This states the requirement outright instead.
  for (const c of markdown.cases) {
    assert.equal(
      typeof c.expected_html,
      "string",
      `${c.name} has no expected_html — regenerate with UPDATE_PREVIEW_FIXTURES=1`,
    );
  }
});

// --- the node walker ------------------------------------------------------------------

const WALKER_FILES = ["cv2_nodes.json", "classic.json", "xss.json"];

let snapshotCases = 0;

for (const file of WALKER_FILES) {
  const data = load(file);
  const emoji = data.emoji || {};
  for (const c of data.cases) {
    if (c.render !== "snapshot") continue;
    snapshotCases += 1;
    test(`${path.parse(file).name}:${c.name} renders to its frozen html`, () => {
      const spec = R.snapshotSpec(c.payload, c.kind);
      assert.equal(
        R.serialize(spec, { emoji, now: NOW_MS }),
        c.expected_html,
        `${c.name} diverged from the Python renderer`,
      );
    });
  }
}

test("the walker corpus actually covered the node kinds", () => {
  // The loops above skip any case whose `render` is not "snapshot". A typo in that
  // field would silently drop coverage rather than fail, so assert the count is in the
  // range the corpus is meant to have.
  assert.ok(
    snapshotCases > 30,
    `only ${snapshotCases} snapshot cases ran — check the fixtures' render fields`,
  );
});

test("serialize and materialize agree, given a DOM", (t) => {
  // materialize() is the back end pages actually use, but it needs a document. Where
  // there is none, this states the contract rather than skipping it silently: the two
  // back ends must draw the same thing from one spec.
  if (typeof document === "undefined") {
    t.skip("no DOM under node --test; the browser lane covers materialize()");
    return;
  }
  for (const file of WALKER_FILES) {
    const data = load(file);
    for (const c of data.cases) {
      if (c.render !== "snapshot") continue;
      const spec = R.snapshotSpec(c.payload, c.kind);
      const host = document.createElement("div");
      R.render(host, spec, { emoji: data.emoji || {}, now: NOW_MS });
      assert.equal(host.innerHTML, c.expected_html, c.name);
    }
  }
});
