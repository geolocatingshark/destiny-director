// Copyright © 2019-present gsfernandes81
//
// This file is part of "dd" henceforth referred to as "destiny-director".
// Licensed under the GNU AGPL v3 or later; see the project LICENSE.

// Unit tests for cv2_model.js — the DOM-free client mirror of cv2_nodes.py.
// Run with `make test-js` (node --test); no browser, no bundler.
//
// The cases worth having here are the ones a rendering bug would hide: the nesting
// rules, path rebasing after a removal (two real bugs found while prototyping), the
// validation paths the UI anchors errors to, and markdown escaping.

const test = require("node:test");
const assert = require("node:assert/strict");

const M = require("../cv2_model.js");

const text = (content) => ({ type: M.TEXT_DISPLAY, content: content });
const container = (children) => ({ type: M.CONTAINER, components: children || [] });
const section = (texts, accessory) => ({
  type: M.SECTION,
  components: texts,
  accessory: accessory,
});
const linkButton = (label, url) => ({
  type: M.ACTION_ROW,
  components: [{ type: M.BUTTON, style: 5, label: label, url: url }],
});

const contents = (list) => list.map((n) => n.content);

// --- classification ------------------------------------------------------------------

test("kind() classifies every authorable type", () => {
  assert.equal(M.kind(text("x")), "text");
  assert.equal(M.kind(container()), "container");
  assert.equal(M.kind({ type: M.SEPARATOR }), "separator");
  assert.equal(M.kind({ type: M.MEDIA_GALLERY }), "media");
  assert.equal(M.kind({ type: M.THUMBNAIL }), "thumbnail");
  // Both a bare button and a button wrapped in an action row are "link_button".
  assert.equal(M.kind({ type: M.BUTTON }), "link_button");
  assert.equal(M.kind({ type: M.ACTION_ROW }), "link_button");
  assert.equal(M.kind({ type: 999 }), "unknown");
  assert.equal(M.kind(null), "unknown");
});

test("makeContainer only seeds an accent when given one", () => {
  assert.equal(M.makeContainer(0xec42a5).accent_color, 0xec42a5);
  assert.ok(!("accent_color" in M.makeContainer()));
});

test("a fresh section starts with one text block, not zero", () => {
  // An empty section is invalid, so starting empty would greet the author with an error.
  assert.equal(M.makeSection().components.length, 1);
});

// --- nesting rules -------------------------------------------------------------------

test("containers are top level only", () => {
  const nodes = [container([])];
  assert.ok(M.allowedIn(nodes, []).includes("container"));
  assert.ok(!M.allowedIn(nodes, [0]).includes("container"));
});

test("a section accepts text blocks only", () => {
  const nodes = [section([text("a")], null)];
  assert.deepEqual(M.allowedIn(nodes, [0]), ["text"]);
});

test("a node cannot be dropped into itself or its own descendants", () => {
  const nodes = [container([section([text("a")], null)])];
  assert.ok(!M.canDrop(nodes, [0], "text", [0]));
  assert.ok(!M.canDrop(nodes, [0, 0], "text", [0]));
});

test("a full section refuses new text but still allows reordering its own", () => {
  const nodes = [section([text("a"), text("b"), text("c")], null)];
  assert.ok(!M.canDrop(nodes, [0], "text", null));
  assert.ok(M.canDrop(nodes, [0], "text", [0, 2]));
});

test("refusalReason explains the rule rather than just refusing", () => {
  const nodes = [container([])];
  assert.match(M.refusalReason(nodes, [0], "container"), /top level only/);
  assert.match(M.refusalReason(nodes, [], "thumbnail"), /accessory/);

  const full = [section([text("a"), text("b"), text("c")], null)];
  assert.match(M.refusalReason(full, [0], "text"), /at most 3/);
  assert.match(M.refusalReason(full, [0], "link_button"), /accessory slot/);
});

// --- path helpers --------------------------------------------------------------------

test("resolve() walks child indices and the accessory segment", () => {
  const acc = { type: M.THUMBNAIL, media: { url: "https://e.invalid/a.png" } };
  const nodes = [container([section([text("deep")], acc)])];
  assert.equal(M.resolve(nodes, [0, 0, 0]).content, "deep");
  assert.equal(M.resolve(nodes, [0, 0, "acc"]), acc);
});

test("childList() returns the real array so callers can splice it", () => {
  const nodes = [container([])];
  M.childList(nodes, [0]).push(text("added"));
  assert.equal(nodes[0].components.length, 1);
});

test("adjustAfterRemoval rebases paths that descend past the removed node", () => {
  // Removing index 0 shifts [1] -> [0], and anything under it.
  assert.deepEqual(M.adjustAfterRemoval([1], [0]), [0]);
  assert.deepEqual(M.adjustAfterRemoval([1, 3], [0]), [0, 3]);
  // Earlier siblings and unrelated branches are untouched.
  assert.deepEqual(M.adjustAfterRemoval([0], [1]), [0]);
  assert.deepEqual(M.adjustAfterRemoval([0, 5], [1]), [0, 5]);
  // Removing an accessory is a field delete — no index shifts at all.
  assert.deepEqual(M.adjustAfterRemoval([1, 2], [0, "acc"]), [1, 2]);
});

// --- mutations -----------------------------------------------------------------------

test("moveNode drags a top-level block into a container that sits below it", () => {
  // The regression that motivated adjustAfterRemoval: removing "A" shifts the
  // container from index 1 to index 0 while we are holding the path [1].
  const nodes = [text("A"), container([text("B")])];
  const at = M.moveNode(nodes, [0], [1], 1);
  assert.equal(nodes.length, 1);
  assert.deepEqual(contents(nodes[0].components), ["B", "A"]);
  assert.deepEqual(at, [0, 1]);
});

test("moveNode reorders within one scope without an off-by-one", () => {
  const nodes = [text("A"), text("B"), text("C")];
  M.moveNode(nodes, [0], [], 3); // A to the end
  assert.deepEqual(contents(nodes), ["B", "C", "A"]);
  M.moveNode(nodes, [2], [], 0); // A back to the front
  assert.deepEqual(contents(nodes), ["A", "B", "C"]);
});

test("moveNode promotes a child out of its container", () => {
  const nodes = [container([text("X"), text("Y")])];
  M.moveNode(nodes, [0, 0], [], 1);
  assert.deepEqual(contents(nodes[0].components), ["Y"]);
  assert.equal(nodes[1].content, "X");
});

test("removeAt returns the next sensible selection", () => {
  const nodes = [text("A"), text("B"), text("C")];
  assert.deepEqual(M.removeAt(nodes, [1]), [1]); // C shifted into slot 1
  assert.deepEqual(contents(nodes), ["A", "C"]);
  assert.deepEqual(M.removeAt(nodes, [1]), [0]); // clamps to the last remaining
  assert.equal(M.removeAt(nodes, [0]), null); // nothing left to select
});

test("removeAt on an accessory deletes the field and selects the section", () => {
  const nodes = [section([text("a")], { type: M.THUMBNAIL, media: { url: "" } })];
  assert.deepEqual(M.removeAt(nodes, [0, "acc"]), [0]);
  assert.ok(!nodes[0].accessory);
});

test("setAccessory unwraps an action row to a bare button", () => {
  // Discord accepts a bare button as a section accessory, never an action row.
  const nodes = [section([text("a")], null)];
  M.setAccessory(nodes, [0], linkButton("Go", "https://e.invalid"));
  assert.equal(nodes[0].accessory.type, M.BUTTON);
  assert.equal(nodes[0].accessory.label, "Go");
});

// --- validation ----------------------------------------------------------------------

test("an empty message is a problem with no path", () => {
  const problems = M.validate([]);
  assert.equal(problems.length, 1);
  assert.equal(problems[0].path, null);
  assert.match(problems[0].msg, /empty/);
});

test("more than 10 top-level blocks is refused", () => {
  const nodes = Array.from({ length: 11 }, (_, i) => text("t" + i));
  assert.ok(M.validate(nodes).some((p) => /Too many top-level/.test(p.msg)));
});

test("problems point at the offending node, not its parent", () => {
  const nodes = [container([text("  ")])];
  const problems = M.validate(nodes);
  const empty = problems.find((p) => /text block is empty/.test(p.msg));
  assert.deepEqual(empty.path, [0, 0]);
});

test("a section reports both arity and a missing accessory", () => {
  const problems = M.validate([section([], null)]);
  assert.ok(problems.some((p) => /1–3 text blocks/.test(p.msg)));
  assert.ok(problems.some((p) => /missing its accessory/.test(p.msg)));
});

test("an incomplete accessory is reported against the accessory itself", () => {
  const nodes = [section([text("a")], { type: M.THUMBNAIL, media: { url: "" } })];
  const problem = M.validate(nodes).find((p) => /thumbnail has no image URL/.test(p.msg));
  assert.deepEqual(problem.path, [0, "acc"]);

  const withButton = [section([text("a")], { type: M.BUTTON, style: 5, label: "x" })];
  const btnProblem = M.validate(withButton).find((p) => /label and a URL/.test(p.msg));
  assert.deepEqual(btnProblem.path, [0, "acc"]);
});

test("a link button needs both a label and a URL", () => {
  assert.ok(M.validate([linkButton("Go", "")]).some((p) => /label and a URL/.test(p.msg)));
  assert.ok(M.validate([linkButton("", "https://e.invalid")]).length > 0);
  assert.equal(M.validate([linkButton("Go", "https://e.invalid")]).length, 0);
});

test("a fully-formed message validates clean", () => {
  const nodes = [
    container([
      text("# Weekly Reset"),
      section([text("body")], { type: M.THUMBNAIL, media: { url: "https://e.invalid/a.png" } }),
      linkButton("More", "https://e.invalid/more"),
    ]),
  ];
  assert.deepEqual(M.validate(nodes), []);
});

// --- markdown ------------------------------------------------------------------------

test("headings, small text and bullets render to the shared md-* classes", () => {
  assert.match(M.renderMd("# Hi"), /md-h1/);
  assert.match(M.renderMd("## Hi"), /md-h2/);
  assert.match(M.renderMd("### Hi"), /md-h3/);
  assert.match(M.renderMd("-# fine print"), /md-small/);
  assert.match(M.renderMd("- item"), /md-bullet/);
});

test("text leaves are escaped", () => {
  const out = M.renderMd("<script>alert(1)</script>");
  assert.ok(!out.includes("<script>"));
  assert.match(out, /&lt;script&gt;/);
});

test("only http(s) links become anchors", () => {
  assert.match(M.renderMd("[x](https://e.invalid)"), /<a href="https:\/\/e\.invalid"/);
  // A javascript: URL is left as literal text — it never reaches an href.
  const bad = M.renderMd("[x](javascript:alert(1))");
  assert.ok(!bad.includes("<a "));
  assert.ok(!bad.includes("href"));
  assert.match(bad, /\[x\]\(javascript:/);
});

test("a quote in link text cannot break out of the href attribute", () => {
  const out = M.renderMd('[a"onerror="x](https://e.invalid)');
  assert.ok(!out.includes('"onerror="'));
  assert.match(out, /&quot;/);
});

test("known emoji shortcodes resolve, unknown ones stay as text", () => {
  const emoji = { kyber: "https://cdn.invalid/1.png" };
  assert.match(M.renderMd("hi :kyber:", emoji), /<img class="emoji"/);
  assert.match(M.renderMd("hi :nope:", emoji), /:nope:/);
  assert.ok(!M.renderMd("hi :nope:", emoji).includes("<img"));
});

test("an emoji URL is escaped into the src attribute", () => {
  const out = M.renderMd(":x:", { x: 'https://e.invalid/a.png"onload="y' });
  assert.ok(!out.includes('"onload="'));
});

test("newlines survive so the pre-wrap canvas keeps line breaks", () => {
  assert.equal(M.renderMd("a\nb").split("\n").length, 2);
});
