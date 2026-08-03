# Web CV2 builder — follow-ups

## Status: §1–§3 are DONE (2026-08-03). §4–§6 stand.

Everything implemented is on `origin/dev` (`74c920f` … `2926827`). The decisions behind it
are in **`plans/web_cv2_builder.md`** — read that first; this file is only what is *left*.

Nothing here is blocking. The builder is in daily-usable shape: authoring, editing and
copying CV2 posts all work on desktop and mobile, and the in-Discord builder it replaced
has been deleted.

---

## 1. Custom emoji on buttons — DONE

The Emoji field stored `{"name": …}`, valid only for a *unicode* emoji; a custom one
needs its id. The page's emoji payload now carries `{url, id, animated}` per name, a
typed name / `:name:` / pasted `<a:name:id>` resolves to `{id, name, animated}`, and
anything unmatched is treated as a literal character. The model accepts both the object
map and the old `{name: url}` one, so rendering-only callers are unaffected.

Two supporting fixes, because the failure was *undetectable* rather than merely wrong:
the canvas now draws a button's emoji (it drew none before), and the field shows what it
resolved to — a matched name renders as its image, an unmatched one does not.

## 2. Media gallery alt text + spoiler — DONE

Both are editable per image and both render: alt into the `img` on the canvas and in
`cv2_render`, spoiler as a blur with a SPOILER overlay so a spoilered image is not
previewed as an ordinary one. `cv2_render`'s thumbnails gained the same two fields —
they had been dropping a description Discord shows — and that render is shared, so the
mirror-log pane gets the alt text too.

## 3. Dead code from the in-Discord builder — DONE

`cv2_nodes.py` went from 631 to ~290 lines; with its tests, **548 lines removed**. The
whole modal-driven layer went: field specs, mutators, the add-flow catalogue, the
tree-op state machine, label helpers, and the `make_*` constructors.

The surviving public API is exactly:

> the type constants, `kind`, `sanitize_for_preview`, `validate`

A correction to this file's earlier claim: the first "verified by grep" pass counted
*test* references as live callers, which overstated what was still in use. Re-measured
separating production from test-only callers — production code outside `cv2_nodes` uses
only `kind`, `sanitize_for_preview` and `validate`. Tests that existed solely to cover
the dead layer went with it; the ones covering surviving behaviour were rewritten
against literal node dicts, which also states the shape under test outright instead of
hiding it behind a constructor.

**Keep it that way:** node construction and tree editing belong to `cv2_model.js` now.

## 4. Deliberately out of scope (record, don't re-litigate)

- **Non-link buttons** (styles 1–4) and **select menus** (types 3, 5–8) need a live
  interaction handler; a posted announcement has none.
- **File components** (type 13) need a real uploaded attachment, not a URL. They
  round-trip when editing an existing post and cannot be authored — the pre-existing
  behaviour, unchanged.
- **Premium/SKU buttons** (style 6) — same reason as non-link buttons.
- **Free-form editing of weekly-reset / Trials posts** — owner call; see
  `plans/web_cv2_builder.md` for the reasoning (they regenerate from `hybrid_post_core`).

---

## 5. Not on prod

The builder has only ever run on the **dev** Railway environment. Promoting it needs the
additive migration **`migrations/20260803030737.sql`** (`cv2_draft`), which the container
entrypoint applies automatically via `atlas migrate apply`. Already exercised against a
real MySQL 8 for the JSON round-trip, creator scoping and the prune `DATETIME` compare.

---

## 6. Interactions no automated test covers

There is no browser on the dev box, so every DOM behaviour below was verified by hand on
a phone, not by a test.

**Driven once in a real Chromium** (Playwright, a throwaway page hosting the widget with
sample nodes over `python3 -m http.server`, at 390×844 with touch and at 1440×900). That
pass found and fixed four defects hand-testing had missed — trailing-emoji backspace,
held-still autoscroll, the posted-message link, the 3-up gallery — plus the phone
confirmation header and the empty-canvas hint. Everything else on the list behaved.

Still **not covered by any committed test**, and still the highest-value addition here:

- pointer-event drag: reorder, re-parent, the accessory slot, edge autoscroll
- long-press → context menu, and its swallowing of the synthesized click
- the contenteditable editor: caret position after accepting an emoji suggestion,
  backspacing across an emoji atom, IME/autocorrect on Android (untried — Chromium's
  synthesized touch is not an IME)
- the properties sheet: dismissal, scroll clearance, re-opening
- the mobile media queries (a cascade-order mistake once silently killed **all** of them,
  and only a screenshot caught it — see the warning comment at the end of
  `cv2_builder.css`). `web_static/tests/cv2_builder_css.test.js` now guards the *ordering*
  that made that failure invisible; it cannot check that the layout is right.

The harness page was deliberately not committed: it is a fixture with no assertions, and
half of what it proved needed a human looking at a screenshot. Turning it into a real
Playwright lane means picking assertions that will not rot — that is the work left.

`web_static/tests/cv2_model.test.js` covers the pure model well (65 tests); it is
specifically the DOM layer that is untested.
