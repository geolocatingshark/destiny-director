# Mirror-log diff schema — three things the port left open

**Status:** filed 2026-08-05, from the code review of `preview-renderer-unification`.
None of these is a live defect; all three are places the diff's Python/JS split is
held together by convention rather than by structure.

Context: the mirror log's version diff is now a tree transform. `dd/anchor/cv2_render.py`
keeps the `difflib` alignment and emits the *new* tree carrying annotations — `_mark` on
a node, `_lines` on a changed text leaf, an `accessory` that may be a list — and
`web_static/cv2_render.js` (`diffSpec`) draws it. The port itself is byte-exact against
what the old Python renderer emitted: all 20 diff fixtures, plus ~9000 randomized
differential cases, produce identical HTML.

## 1. The annotations are in-band, in Discord's own key namespace

`diffSpec` hands the annotated tree to `nodesSpec` — the same function `snapshotSpec`
uses for an *unmodified* captured payload. Nothing in the renderer distinguishes "the
server aligned this" from "this key was in the message". A payload that arrived carrying
`_lines` would make a plain snapshot draw a diff that never happened; `_mark` would paint
arbitrary nodes green and red.

What actually prevents it is two modules away, in the other bot: `mirror_worker`'s
`_snapshot_payload` re-serializes every CV2 tree through hikari's builders (`build()[0]`),
which emit only fields Discord defines and cannot carry an unknown key. That invariant is
now asserted where it holds
(`dd/beacon/tests/test_mirror_version_snapshot.py::test_a_snapshot_cannot_carry_the_diff_renderer_s_annotations`),
which was the cheap half of the fix.

The structural half is still open: moving the annotations into a `_diff` sub-object (or a
path-keyed side table) would make the confusion unrepresentable and let `snapshotSpec`
reject an annotated payload outright rather than draw it. Note `_lines` also leaves
`content` in place carrying the same text twice.

## 2. A pair that differed can render completely unmarked

Two shapes produce a diff that reads as "nothing changed" while `unchanged` is false and
no note is emitted:

- An action row whose changed child is not a link button. `actionRow` only ever calls
  `button()`, so an annotated non-button child contributes nothing. (The old Python diff
  drew a placeholder here — but its own *snapshot* path dropped non-buttons too, so old
  snapshot and old diff disagreed. The new behaviour is the more consistent one, which is
  why this is a follow-up rather than a regression.)
- A container whose only change is its own attributes, e.g. `accent_color` 1 → 2 with
  identical children. `_annotate_pair` recurses into `components` and rebuilds
  `{**new, …}` without comparing the parent's other fields. Identical on `dev`.

Python already knows the pair differed. A `_mark: "changed"` on the merged node — plus a
`.cv2-changed` rule beside the existing `.cv2-added`/`.cv2-removed` in `mirror_log.css` —
would close both cases at once.

## 3. `note` ships as English prose

`mode` distinguishes truncated and format-changed, but inside `mode: "diff"` the client
can only string-match to know whether it is looking at "No changes from the previous
version." A note *code* (`"unchanged"` / `null`) alongside the text would let the client
style or suppress it without reading English.

## Already fixed, recorded so they are not re-filed

- `diffText` had no explicit `replace` arm, so an unrecognised op fell through it and,
  with no `runs`, deleted the line outright; an empty `_lines` erased the whole leaf.
  Both now degrade to the plain line / to `content`.
- Every child collection now goes through an `Array.isArray` guard, so a malformed
  `components`/`items`/`embeds`/`_lines` degrades instead of throwing.
- `_word_runs` was unbounded: `SequenceMatcher(autojunk=False)` over whitespace-preserving
  tokens measured **1.4 s of blocked event loop** on a ~4000-character block, synchronously
  inside an `async` handler in the bot's own process, on text from someone else's server.
  Capped at `_WORD_DIFF_TOKEN_CAP` tokens, above which the block degrades to one
  delete + one insert (0.0008 s for the same input).
- `_accessory_renders` restates a *rendering* rule the JS owns — the one place the
  "Python aligns, JS draws" line genuinely leaks. Now held by a paired tripwire
  (`ACCESSORY_KINDS` in `cv2_render.py`, and "the renderer draws exactly the accessory
  kinds Python knows about" in `preview_fixtures.test.js`); a third kind added to either
  side fails the other.
