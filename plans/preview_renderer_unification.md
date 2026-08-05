# Preview renderer unification — one shared CV2/classic renderer

## Status: DONE (2026-08-05) — kept only for the record; delete when it stops earning it

All seven phases landed on `preview-renderer-unification`. There is one renderer,
`dd/anchor/web_static/cv2_render.js`, and every preview surface draws through it.

Where the durable knowledge went, since this file is not the place to look for it:

- **`docs/architecture.md`** — "Rendering a message on the web", the seam and its two
  consequences (untrusted content, the golden corpus).
- **`dd/anchor/preview_fixtures/README.md`** — the corpus contract and how to regenerate.
- **`dd/anchor/extensions/cv2_builder_page.py`** — the trust boundary, and what `/preview`
  is *for* now that it returns a tree rather than markup.
- **`dd/anchor/cv2_render.py`** — why the diff stayed in Python and what its annotations
  mean.

What the plan did not do, and why:

- **A CSP header** (`script-src 'self'`) is still worth adding as the backstop for
  client-rendered untrusted content. It needs the three `__BOOTSTRAP__` inline blocks
  turned into `<script type="application/json">` and `autopost_settings.html`'s inline
  handler extracted to a file — the second of which is an unrelated page, so it was left
  as its own piece of work rather than folded in here.
- **Viewer-local `<t:…>` timestamps.** Both implementations apologised in comments for
  rendering UTC because a server cannot know the viewer's zone. A client renderer can,
  and that is what Discord does. Deliberately deferred so it did not muddy the fidelity
  comparison during the port; it is now a small, isolated change to `_timestampText`.

## Context

The anchor web UI renders Discord message previews **twice, in two languages**, and the
two have already drifted.

**Renderer A — Python, server-side** (`dd/anchor/cv2_render.py` 605 lines +
`hybrid_post_core.py` ~172–514 + `cv2_html.py`). Walks a CV2 node tree — or a classic
`content`+`embeds` payload — into a trusted pre-escaped HTML string, returned over HTTP
and dropped into `innerHTML`. It also owns a recursive structural + word-level **diff**
(`render_diff`, built on stdlib `difflib`). Feeds the mirror log, the publish
confirmation, and — through a *second, separate* flat renderer emitting a different class
vocabulary (`.post-*` vs `.cv2-*`) — the weekly-reset / trials / rotation previews.

**Renderer B — JavaScript, client-side** (`web_static/cv2_model.js` `renderMd` +
`cv2_builder.js` `renderBody`/`renderScope`/`renderAccessory`). Repaints the builder
canvas on every keystroke and drag. The canvas *is* the editing surface, so a server
round-trip per keystroke is impossible — which is why B exists at all.

Both target the same stylesheet, `web_static/cv2_preview.css`, so they are *supposed* to
look identical. They don't.

### The drift, measured

B is missing what A learned. Every one of these must land in the unified renderer:

| # | Behaviour | A (Python) | B (JS canvas) |
|---|---|---|---|
| 1 | Heading spacing normalised | `_normalize_heading_spacing` applied | not applied |
| 2 | Gallery tile links to full image | `<a href target=_blank rel=noopener>` | bare `<span><img>` |
| 3 | Media/thumbnail URL scheme check | `_media_url` drops non-`http(s)` | interpolates any string |
| 4 | Thumbnail `alt` + spoiler | `description` → `alt`, `cv2-spoiler` | `alt=""`, spoiler ignored |
| 5 | File component (type 13) | "File attachment (from the original post)" | "Unsupported component (type 13)" |
| 6 | Placeholder marker | `⚠️ ` prefix | none |
| 7 | Buttons | link → real `<a>`, url-validated, emoji drawn | always inert `<span>` |
| 8 | `.cv2-root` wrapper | present (CSS keys flex/gap/font-size off it) | absent — those rules never apply |
| 9 | Classic message + embeds | full support | none |
| 10 | `sanitize_for_preview` first | yes | no |

Four things flow the *other* way and must be preserved:

| # | Behaviour | A (Python) | B (JS canvas) |
|---|---|---|---|
| 11 | Inline `` `code` `` spans | no arm in `_INLINE_MD` (`:187`) | `code` arm (`cv2_model.js:527`) |
| 12 | Bullet markers | `- ` only (`:328`) | `- ` **and** `* ` (`cv2_model.js:671`) |
| 13 | Separator `spacing === 2` | ignored | honoured — but as an **inline style**, breaking the shared-stylesheet contract; must become a class |
| 14 | Authoring placeholders | generic | "Empty container — drop blocks in." — better on the builder surface, keep them there |

Items 11 and 12 are the sharpest evidence that this is not a hypothetical problem: the
publish-confirmation dialog tells the author *"This is exactly how Discord will render
it"* (`cv2_builder.js:2313`), and today a backtick span or a `* ` bullet renders one way
on the canvas and a different way in that confirmation. The unified inline whitelist is
therefore `{span, strong, em, code, a, img}` — one place, stated once.

### Decided

- **One shared JS renderer.** JS survives, because the per-keystroke canvas cannot round
  trip to the server. Python loses all HTML emission.
- **All four surfaces migrate**: builder canvas + publish confirmation, mirror-log
  snapshot, mirror-log diff, and the weekly-reset / trials / rotation `.post-*` previews.
- **The diff stays in Python** (stdlib `difflib`) but is reworked to emit an *annotated
  tree* the shared renderer draws — the diff becomes a tree transform, not a renderer.
- **The renderer emits a spec tree, materialized into DOM** — not HTML strings. See below
  for why; this is the decision that makes the untrusted-content move safe *and* keeps the
  walker testable under `node --test`.

## Constraints that shape everything

- No bundler, no npm, no `package.json`, no ES modules. Classic `<script>` tags,
  `window.*` globals; HTML load order *is* the dependency graph.
- `make test-js` = `node --test "dd/*/web_static/tests/*.test.js"` — no DOM, no jsdom,
  zero dependencies. Only the `cv2_model.js` footer pattern (`module.exports` +
  `window.*`) makes a file testable.
- Python-only runtime on Railway; Node exists in CI only.
- The mirror log renders **untrusted third-party content** and is moving from a server
  renderer to a client one. `cv2_html.py:35–45` currently argues the client render is safe
  *because* the mirror log stays server-rendered — this migration deletes that premise, so
  the safety argument has to be rebuilt, not inherited.

## Design

### The shape

```
                        web_static/cv2_model.js        (unchanged: markdown, emoji, <t:>,
                                 ▲                      node kinds, validation — 81 tests)
                                 │
                        web_static/cv2_render.js       ← NEW. The ONE renderer.
                                 ▲                        CV2 tree · classic+embeds ·
             ┌───────────────────┼───────────────────┐    diff annotations · post specs
             │                   │                   │
     cv2_builder.js       mirror_log.js        shared.js
     (canvas + confirm)   (snapshot + diff)    (initPostPreview)
```

Server routes stop emitting HTML and emit **payload JSON**. Python keeps only what must
be server-side: `sanitize_for_preview`, `validate`, emoji-dict resolution, snapshot
loading, and the **diff alignment** (`difflib`) — now expressed as tree annotations
rather than markup.

### Reuse, don't reinvent

`cv2_model.js` already carries most of the leaf layer, tested to 81 cases:
`renderMd`/`lineMd`/`inlineMd` (the ordered single-pass `INLINE` regex — note the comment
at `cv2_model.js:520` explaining why a `.replace()` chain mangles real posts),
`_timestampText` (mirrors `_format_ts`), `esc`, and `_emojiHtml`, which **already merges
both server substituters** — CDN-resolved `<:name:id>` (the mirror-snapshot shape) *and*
name-map `:name:` (the authored shape). `cv2_render.js` consumes these; it does not
re-implement them.

What is genuinely new in JS is the **node walker** — roughly `cv2_render.py:119–360`
(container / section / media / separator / button / thumbnail / action row / file /
placeholder, plus classic + embed) — around 240 lines of straightforward port.

### The renderer emits a spec tree, not HTML

Neither "port the string concatenation" nor "call `createElement` everywhere" is right.
The walker is **pure and emits a plain-data spec**; a small browser-only **materializer**
turns specs into elements:

```js
{ tag: "div", cls: "cv2-container", accent: 0xaabbcc, children: [...] }
{ tag: "div", cls: "cv2-text", md: node.content }   // the ONE innerHTML field
{ tag: "a",   cls: "cv2-button", url: node.url, children: [{ text: label }] }
{ tag: "img", cls: "cv2-thumb", url: media.url, alt: node.description }
```

The materializer (~50 lines) is the single chokepoint: `text` → `textContent`; `url` →
the `http(s)` check applied **once, centrally**, then `setAttribute`; `accent` →
`el.style.borderLeftColor` (a property assignment, which cannot escape); `md` →
`innerHTML` of `renderMd(...)`, the only field permitted to reach that sink.

Why this and not strings:

- **Safety is structural, not remembered.** `cv2_render.py` re-states the `http(s)` check
  at six separate emit sites (`:94`, `:147`, `:159`, `:207`, `:306`, and
  `hybrid_post_core.py:292`); a string port carries that obligation to every future emit
  site, in the same change that starts feeding it untrusted content. Under the
  materializer, forgetting is not expressible.
- **It is *more* testable under `node --test`, not less.** The walker is pure data in,
  pure data out — no DOM needed. Assertions become "this node produced a `cv2-media` with
  three children and the alt text landed in `text`, not `md`", which is a better test
  than substring-matching HTML. The materializer is covered once by the existing
  Playwright lane.
- **The builder barely notices.** Its selection, drag, landing and scroll-to-problem
  machinery is keyed on `data-path` strings re-queried after every paint
  (`cv2_builder.js:1435`, `:1852`, `:988`, `:1505`), never on element identity —
  `cv2_builder.css:381` states that as a design invariant. `paintCanvas` (`:311`) becomes
  `replaceChildren(...)`; the one surgical `blk.outerHTML = …` (`:1205`) becomes
  `replaceWith(...)`. And the ~300 lines that would churn are the ones this migration
  rewrites anyway.
- **Performance is a non-issue.** Typing in the contenteditable triggers no canvas render
  at all (commit on blur, `:1185`); trees are capped at 10 top-level nodes, depth 3, 4000
  chars. Spec-build + `replaceChildren` skips the HTML parser entirely.

**One sanctioned string island:** `inlineMd`/`lineMd`/`renderMd` stay as they are. They
are escape-by-construction, cover both emoji shapes, carry 65 tests and hard-won history
(the single-ordered-pass rationale at `cv2_model.js:519` — a `.replace()` chain once
mangled real posts by substituting into HTML earlier passes had emitted). Freeze that
island; grow the tag whitelist only in the materializer.

### The `.post-*` vocabulary gets retired, not ported

`hybrid_post_core.build_cv2` (`:147`) builds exactly `container(text_display,
[media_gallery], [separator, action_row])`. `render_post_html` (`:386`) is a *second*
renderer that approximates that same structure in a different class vocabulary
(`.post-preview`/`.post-image`/`.post-buttons`, `shared.css:154–200`) — while already
sharing `.md-*` and `img.emoji` with the CV2 sheet.

So the weekly-reset / trials / rotation previews should render the **real node tree**,
not an approximation of it. Add `post_spec_nodes(spec) -> list[Node]` beside `build_cv2`
(one test pinning the two to the same structure), have `/preview` return that tree, and
delete `render_post_html` / `render_post_spec` / `render_post_wall` and the `.post-*`
block in `shared.css`. This is a fidelity *upgrade*: the preview becomes the post.

`.post-wall*` (the rotation editor's stacked labelled cards) is layout, not post
rendering — it stays, wrapping `.cv2-preview` bodies.

### The diff becomes a tree transform

Read `cv2_render.py:362–605`. Every diff behaviour is expressible as annotations on the
rendered stream, because the diff never invents markup — it only wraps and marks:

| Python today | Annotation |
|---|---|
| `_added_node` / `_removed_node` → `.cv2-added`/`.cv2-removed` wrapper | `_mark: "added" \| "removed"` on the node |
| `_diff_pair` on a changed text leaf → `_line_diff_html` | `_lines: [...]` replacing `content` |
| `_line_diff_html` equal / delete / insert / replace | per-line `{op, line}` or `{op:"replace", runs:[{op,text}]}` from `_word_diff_html` |
| `_diff_accessory` showing both old and new | `accessory` becomes a *list* of marked nodes |
| `_diff_nodes` / `_diff_replace` alignment | pure Python; emits the marked child list |
| `render_diff`'s notes (truncated, format-changed, no-change) | a top-level `note` string |

Python keeps the `difflib` alignment and the word/line tokenisation — the hard,
well-tested part. JS gains one extra arm per node ("if `_mark`, wrap it") and one text
arm ("if `_lines`, draw ins/del runs"). The `ins`/`del`/`.cv2-added`/`.cv2-removed`
styles stay in `mirror_log.css:327–352`; only the mirror log diffs.

**This is the riskiest piece of the whole migration and the easiest to under-weight.**
Everything else is a port of code that already exists on one side; the diff schema is a
brand-new cross-language wire format, produced by untyped Python, consumed by untyped JS,
carrying the most untrusted content in the system, and pinned today by only a handful of
tests. Design it *from* `cv2_render.py:390–501`, not from the plain render, because the
behaviour is subtler than it looks:

- equal lines keep their **markdown rendering**;
- inserted lines render markdown *inside* `<ins>`;
- deleted lines are **raw escaped text** inside `<del>` — not markdown;
- a replace-run is word-diffed over the **raw** text of the whole block
  (`"\n".join(...)`), with a tokenizer that preserves whitespace runs (`_WORD`, `:370`);
- `_diff_replace` pairs components **positionally** (`:488`), and the accessory has a
  three-state (added / removed / both, `:438`).

A naive per-line schema cannot express the replace-run; a naive per-word one loses
"equal lines keep markdown". The schema must therefore carry **pre-split segments** so JS
only *draws* and never re-diffs — which also keeps the trust math trivial: every segment's
`text` goes through the materializer's `text` field, and `ins`/`del` are structural
wrappers in the spec.

### Trust boundary — the thing to get right

The mirror log renders **other people's captured posts**. Today Python escapes them;
after this, JS does. What must hold, and be tested:

- Every text leaf lands in the spec's `text` field → `textContent`. Injection is not
  expressible.
- Every URL `http(s)`-validated in the materializer's **one** `url` handler and dropped
  if it fails — matching `_is_http_url`/`_media_url`. `cv2_builder.js:452` currently
  performs no such check, and that gap must not survive the merge.
- Emoji `id` matched against `/^\d+$/` before it is concatenated into a CDN URL
  (`cv2_model.js:551` already does).
- Only `md` reaches `innerHTML`, and only ever with `renderMd` output.
- The server still runs `sanitize_for_preview` + `validate` before publish regardless of
  what the client believed (`cv2_builder_page.py:210`). That gate is **untouched** by
  this migration — say so explicitly in whatever inherits `cv2_html.py`'s docstring,
  which is currently the repo's best statement of the trust design and must not die
  silently with the module.

### Replacing the lost independent check

Today the publish-confirmation dialog is a **second, independent implementation**
rendering the same tree (`cv2_builder.js:2193` — "The server render is the authoritative
one — confirm against it"). After unification it is the same code as the canvas, so that
property is gone.

What replaces it: `POST /cv2-builder/{draft}/preview` returns the **server-sanitized,
server-validated node tree** instead of HTML. The confirmation still shows something the
server vouched for — the *data* stays authoritative even though the renderer is shared —
and it now visibly differs from the canvas exactly where `sanitize_for_preview` changed
something, which is the useful signal. The golden-fixture corpus below is what replaces
the accidental cross-check the two implementations gave each other.

### Regression safety: a shared golden corpus

New `dd/anchor/preview_fixtures/*.json` — each case a `{name, kind, payload, emoji, now,
expected_spec}` record. Exercised by **both** sides:

- `dd/anchor/tests/test_preview_fixtures.py` — asserts the Python diff-annotation layer
  produces the expected annotated tree.
- `dd/anchor/web_static/tests/cv2_render.test.js` — asserts the JS walker turns each
  fixture into the expected spec tree.

Written **before** the port and generated from today's Python output, so the port is
provably behaviour-preserving for everything except the fourteen drift items — each of
which gets its own fixture asserting the *new*, corrected behaviour. For the diff,
additionally capture golden `render_diff` HTML from a corpus of **real stored version
pairs** and compare the new pipeline's rendered output against it (class names aside);
the diff is where a schema mistake will hide.

**Do not let test depth regress.** CI exercises the render path today through
`test_cv2_render.py` (18), `test_cv2_html.py` (12) and `test_hybrid_post_core.py` (16).
If those die with their modules and only the model tests survive in JS, effective
coverage of rendering drops exactly when it starts taking untrusted input. Port them to
spec-tree assertions as part of each surface's migration, not as follow-up.

## Phases

Each phase ends with `make check` green, and each ships independently.

**Phase 0 — freeze current behaviour.** Generate `dd/anchor/preview_fixtures/` from
today's `render_snapshot` / `render_diff` / `render_post_spec` output across every node
kind, the classic+embed path, the sanitize path, and XSS probes. Wire
`test_preview_fixtures.py` to assert Python still matches. Nothing else changes.

**Phase 1 — `cv2_render.js` + materializer.** Port the walker as a pure spec emitter,
consuming `cv2_model.js`'s leaf layer; add `_normalize_heading_spacing` to `cv2_model.js`
and apply it in `renderMd` (drift 1). Add `tests/cv2_render.test.js` asserting the corpus
as spec trees, and the `asset_links.test.js` load-order guard. At this point the renderer
exists and is proven, but nothing uses it.

**Phase 2 — builder canvas onto the shared renderer.** `cv2_builder.js`'s
`renderBody`/`renderAccessory` (`:411–545`) delegate to `cv2_render.js`, keeping only
editor chrome (`.cv2b-blk`, `data-path`, grips, rails, accessory slots, the
contenteditable swap) and its authoring placeholders. `paintCanvas` (`:311`) →
`replaceChildren`; the surgical `blk.outerHTML =` (`:1205`) → `replaceWith`. All fourteen
drift items land here. Wrap the canvas in `.cv2-root` and replace the separator's inline
style with a class. `test_builder_drag.py` (the Playwright lane) must stay green — it is
the only coverage of the drag layer, and this is the phase most able to break it.

**Phase 3 — publish confirmation.** `POST /cv2-builder/{draft}/preview` returns
`{nodes}` (sanitized + validated) instead of `{html}`; `cv2_builder_page.js:101` and
`cv2_builder.js:2199` render it with the shared module.

**Phase 4 — retire `.post-*`.** Add `post_spec_nodes()` beside `build_cv2`; the
weekly-reset / trials / rotation preview routes return node trees; `shared.js`
`initPostPreview` (`:42–80`) renders with the shared module — one function, and both
hybrid-post forms inherit it. Delete `render_post_html`/`render_post_spec`/
`render_post_wall`, `hybrid_post_core`'s `_render_inline`/`_render_line`/
`_normalize_heading_spacing`/`_format_ts`/`_html_emoji_substituter` (**verified**: no
non-preview callers), and the `.post-*` block in `shared.css`. Swap the two
`<pre id="previewBox" class="post-preview">` for `.cv2-preview` divs. Do the deletion in
the same change as the migration — a half-migrated third vocabulary is worse than either
endpoint.

**Phase 5 — mirror-log snapshot.** `GET /mirror-logs/render` returns the snapshot payload
as JSON; `mirror_log.js` `renderCol` (`:471`) renders it. Requires the classic + embed
arms of `cv2_render.js`. Needs **no emoji map** — captured content carries full
`<:name:id>`, which `cv2_model.js:547` already resolves off the CDN, matching
`_cdn_emoji_substituter` (`cv2_render.py:67`).

**Phase 6 — mirror-log diff.** Rework `cv2_render.py`'s diff half into the annotation
emitter; add the two annotation arms to `cv2_render.js`. Delete all HTML emission from
`cv2_render.py`; `cv2_html.py` collapses to a sanitize call at the route.

Phases 5 and 6 are deliberately **last**: they are the only surfaces carrying untrusted
content, so the renderer arrives at them already battle-tested on owner-authored posts.
Each Python renderer is deleted in the same change that migrates its last consumer.

### Two things to do alongside

- **A CSP header on the anchor app.** `script-src 'self'` + `object-src 'none'` in a
  `web.py` middleware is a few lines and is exactly the backstop worth having when
  untrusted content moves to a client renderer. Check the `/*__BOOTSTRAP__*/ null`
  injection first (`rotation_editor.py:343` and the three form pages) — if it is an
  inline `<script>` body it must become a `<script type="application/json">` block, which
  CSP permits.
- **Timestamps become correct, as a separate commit.** Both current implementations
  apologise in comments for rendering `<t:…>` in UTC because the server cannot know the
  viewer's zone (`hybrid_post_core.py:197`, `cv2_model.js:558`). Client-side rendering
  can just use the viewer's zone, which is what Discord actually does. Take the win —
  but *after* the port lands, so it doesn't muddy the fidelity comparison.

## Files

**Create**
- `dd/anchor/web_static/cv2_render.js` — the single renderer: pure spec-emitting walker
  plus the browser-only materializer. Dual `module.exports` + `window.CV2Render` footer
  (the `cv2_model.js:984` pattern) so `node --test` can load it, with its dependency made
  explicit at the top:
  `const M = typeof require !== "undefined" ? require("./cv2_model.js") : window.CV2Model;`
  Keep it a **separate file** from `cv2_model.js` — that file is already 986 lines
  spanning model + markdown + geometry + editor segmentation, and mirror_log needs
  model+render without the builder.
- `dd/anchor/web_static/tests/cv2_render.test.js` — the corpus, JS side.
- `dd/anchor/preview_fixtures/*.json` — the shared golden corpus.
- `dd/anchor/tests/test_preview_fixtures.py` — the corpus, Python side.

**Modify**
- `dd/anchor/web_static/cv2_model.js` — port `_normalize_heading_spacing`; apply it in
  `renderMd` (drift 1). Export `esc` (already exported) and the emoji helpers to
  `cv2_render.js`.
- `dd/anchor/web_static/cv2_builder.js` — `renderBody`/`renderAccessory` (`:411–545`)
  delegate; keep editor chrome and authoring placeholders.
- `dd/anchor/web_static/cv2_preview.css` — add the separator-spacing class replacing the
  inline style; add `.cv2-root` where the canvas now needs it.
- `dd/anchor/web_static/mirror_log.js` (`renderCol`, `:461–494`), `shared.js`
  (`initPostPreview`, `:41–81`), `editor.js` (`runPreview`, `:813`),
  `cv2_builder_page.js` (`:101`) — render payloads instead of injecting server HTML.
- `dd/anchor/cv2_render.py` — HTML emission deleted; diff reworked to emit annotations.
- `dd/anchor/hybrid_post_core.py` — add `post_spec_nodes()`; delete the whole "Rich HTML
  preview" section (`:172–514`). **Verified**: `_format_ts` and
  `_normalize_heading_spacing` have no non-preview callers. `_format_reset_ts` and
  `_relative_ts` are unrelated and stay.
- `dd/anchor/extensions/{mirror_log,cv2_builder_page,rotation_editor}.py`,
  `extensions/{weekly_reset,trials}.py` — routes return JSON.
- HTML shells — `mirror_log.html`, `editor.html`, `trials_form.html`,
  `weekly_reset_form.html`, `tests/builder_harness.html` gain `cv2_model.js` +
  `cv2_render.js` script tags; the two `<pre class="post-preview">` become
  `<div class="cv2-preview">`.
- `dd/anchor/web_static/tests/asset_links.test.js` — new rule, in the same spirit as the
  existing CSS-pairing guards: any page including `cv2_render.js` must include
  `cv2_model.js` **earlier in the document**, and any page loading `cv2_preview.css` must
  load both. This is what makes "load order is the dependency graph" safe at four files
  instead of two.
- `dd/anchor/web.py` — the CSP middleware.
- `dd/anchor/web_static/shared.css` — delete the `.post-*` preview block (`:154–200`),
  keep `.post-wall*` layout.
- `docs/architecture.md` — it currently mislabels the `cv2_*` modules as "OpenCV image
  generation" and names a `dd/anchor/cv2_builder.py` that no longer exists. Fix that and
  document the single-renderer seam.

**Delete**
- `dd/anchor/cv2_html.py` (collapses to a sanitize call at the route).
- Python render tests as they migrate: `test_cv2_render.py` (18), `test_cv2_html.py` (12)
  and `test_hybrid_post_core.py` (16) — 46 render tests, most re-landing as JS spec-tree
  cases against the corpus. `test_mirror_log_render.py` (9) is route-level and is
  repointed at the JSON payload rather than deleted.

## Verification

- `make check` (lint + typecheck + pytest + `node --test`) green at every phase. Baseline
  today is 91 JS tests passing.
- `make test-browser` — `test_builder_drag.py` drives the real drag layer in Chromium and
  is the only coverage of it; it must stay green through Phase 2. Needs
  `uv run playwright install chromium` first.
- The corpus is the acceptance test: same fixture, same output, Python and JS.
- Manual, on the dev Railway environment: build a post in the web builder, confirm the
  canvas, the confirmation dialog and the real Discord send all agree; open the mirror
  log and check a captured classic message, a CV2 message, and a diff between two
  versions of each.
- XSS probes in the corpus: `<script>` in every text leaf, `javascript:` in every URL
  slot (button, link, media, thumbnail, embed image), a non-numeric emoji id, and a
  `"`-bearing alt text.

## Risks

1. **The diff annotation schema — the one piece with no existing counterpart.**
   Everything else is a port of code that exists on at least one side. This is a new
   cross-language wire format, untyped on both ends, carrying the most untrusted content
   in the system, encoding the five subtleties listed above. Design it from
   `cv2_render.py:390–501`, and pin it with golden output from real stored version pairs
   before migrating.
2. **Test-depth regression.** 46 Python tests currently cover rendering. If they die with
   their modules and don't re-land as spec-tree assertions, coverage drops exactly when
   the renderer takes on untrusted input.
3. **Untrusted content moves to a client renderer.** Structurally mitigated by the
   materializer chokepoint, backed by XSS probes in the corpus and the CSP header — but
   this is the change that would hurt most if it went wrong. An XSS here runs in an
   authenticated bot-owner session on an admin UI that can publish to Discord.
4. **`cv2_builder.js` is 2326 lines.** Its selection/drag machinery is keyed on
   `data-path` strings rather than element identity, so the switch to DOM building is
   safe in principle — but Phase 2 still rewrites the code every gesture lands on. The
   Playwright lane is the safety net, and `plans/web_cv2_builder_followups.md` §6 lists
   what that lane still does *not* cover (long-press menus, contenteditable caret
   behaviour, the properties sheet, the mobile media queries).
5. **CSS convergence is eyeball territory.** Retiring `.post-*` restyles three live
   forms, and the browser lane deliberately asserts behaviour, never appearance. Budget a
   manual pass — per the §6 lesson that a cascade-order mistake once silently killed
   *every* mobile media query and only a screenshot caught it.
6. **No module system.** A load-bearing renderer joins a `window.*` global graph where
   script order is the dependency graph. That still scales here, with the
   `asset_links.test.js` guard as part of the work rather than an afterthought. Worth
   recording for someday: the one constraint whose loosening would delete this whole
   category of problem is the ES-module ban — `<script type="module">` needs no bundler
   and `node --test` runs ESM natively. The likely reason for the ban is `file://`
   fixtures like `builder_harness.html`, where modules don't load. Not a recommendation,
   just a flag.

## Plans housekeeping

Per the repo rule on `plans/`: this work closes out the preview half of
`plans/web_cv2_builder.md` and `plans/web_cv2_builder_followups.md` §6's testing gap.
Update or remove those entries as phases land rather than at the end.
`plans/web_embed_builder.md` is unexecuted and depends on the embed render path — its
§2 (`render_embed_html` in `hybrid_post_core`) is obsoleted by this plan and should be
rewritten to target `cv2_render.js` instead.


