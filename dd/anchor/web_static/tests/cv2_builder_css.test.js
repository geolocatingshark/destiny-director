// Copyright © 2019-present gsfernandes81
//
// This file is part of "dd" henceforth referred to as "destiny-director".
// Licensed under the GNU AGPL v3 or later; see the project LICENSE.

// A cascade-order guard for cv2_builder.css.
//
// The narrow-screen block overrides base rules of the SAME specificity (.cv2b-palette,
// .cv2b-inspector, .cv2b-rail, …), so its position in the file is the only thing making
// it win. It once sat earlier in the sheet and silently did nothing: on every phone the
// palette kept its desktop column and the inspector never became a sheet. Nothing
// failed, nothing logged — only a screenshot caught it.
//
// This is a text check, not a rendering one; it cannot prove the mobile layout is right
// (that needs a browser). It proves the one property that made the failure invisible:
// the overrides come last, and each selector they override really does appear earlier.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const CSS = fs.readFileSync(path.join(__dirname, "..", "cv2_builder.css"), "utf8");
const NARROW = "@media (max-width: 900px)";

/** Everything after the narrow block's closing brace, comments and blanks stripped. */
function tail() {
  const start = CSS.indexOf(NARROW);
  let depth = 0;
  for (let i = start; i < CSS.length; i++) {
    if (CSS[i] === "{") depth++;
    else if (CSS[i] === "}" && --depth === 0) {
      return CSS.slice(i + 1)
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .trim();
    }
  }
  throw new Error("the narrow-screen media query is never closed");
}

test("the narrow-screen overrides exist", () => {
  assert.ok(CSS.includes(NARROW), "cv2_builder.css has no " + NARROW + " block");
  assert.equal(CSS.indexOf(NARROW), CSS.lastIndexOf(NARROW), "expected exactly one");
});

test("nothing follows the narrow-screen overrides", () => {
  // Anything here would out-cascade them at equal specificity, which is how every
  // mobile rule in this sheet was once silently disabled.
  assert.equal(tail(), "");
});

test("each selector the narrow block overrides is defined before it", () => {
  const narrow = CSS.slice(CSS.indexOf(NARROW));
  const base = CSS.slice(0, CSS.indexOf(NARROW));
  // The rules whose desktop form the phone layout has to beat; if one is missing from
  // either half, the pairing has drifted and the override is aimed at nothing.
  for (const sel of [
    ".cv2b-palette",
    ".cv2b-inspector",
    ".cv2b-canvas-wrap",
    ".cv2b-rail",
    ".cv2b-status",
    ".cv2b-dlg-head",
  ]) {
    assert.ok(base.includes(sel), sel + " has no base rule to override");
    assert.ok(narrow.includes(sel), sel + " is not overridden for narrow screens");
  }
});
