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
// Today this covers the markdown leaf layer — the narrowest place the two meet. The
// node walker joins it when cv2_render.js lands, at which point the remaining fixture
// files switch on here too.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const M = require("../cv2_model.js");

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
