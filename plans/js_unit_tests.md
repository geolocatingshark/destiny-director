# STUB: unit tests for the anchor web-form JS (shared.js)

> **Status: stub / deferred.** Written alongside the web-form sharing work
> (`anchor/share-web-form-client`), which moved real logic — the live previewer and the
> whole create/edit/delete/autopost lifecycle — out of the per-form scripts into
> `dd/anchor/web_static/shared.js`. That logic is now shared by both forms (and, soon, the
> user-commands manager), so it's worth testing directly instead of only smoke-testing each
> form by hand. There is **no JS test toolchain in this repo yet** (it's a uv/Python
> project, no `package.json`, no build step), so standing one up needs a deliberate call —
> hence this stub rather than tests.

## Why now

`shared.js` grew from an 18-line `api()` helper into ~250 lines with branching logic that a
manual browser smoke can't cover exhaustively:

- **`initPostForm` button visibility** — `updateButtons()` across the full
  `postThisPeriod × crossposted` matrix (which of Create / Create&publish / Edit /
  Edit&publish / Delete show).
- **`postAction` response handling** — the three branches: `data.problems` (blocked,
  publish path), `!res.ok || !data.ok` (request failed), and success (re-sync
  `postThisPeriod`/`crossposted` from the response, advisory `warnings`, the `note`/warning
  status string).
- **`initPostPreview` render** — ok → `innerHTML` (server returns SAFE HTML), failure →
  `textContent` (untrusted body stays escaped); the debounce; the `--post-accent` set.
- **delete** — confirm-wording selection (`labels.deletePublished` vs `labels.deleteDraft`
  by `crossposted`) and the post-delete state reset.

These are the exact places a future edit could silently regress (e.g. a wrong button shows,
or an error body gets `innerHTML`'d — an XSS footgun).

## What to test (priority order)

1. `updateButtons` truth table (pure DOM assertions on `.hidden`).
2. `postAction` branches (mock `fetch`/`api`, assert status text + button re-sync + that
   `showProblems` renders via `textContent`, never `innerHTML`).
3. `initPostPreview.render` ok vs failure path — **assert the failure body is escaped**
   (security-relevant).
4. delete confirm-wording + state reset.
5. `readForm` payload shape per form (trials vs weekly_reset) — guards the server
   `_context_from_payload` contract from the client side.

## Toolchain options (pick one — needs user buy-in)

- **A. Vitest + jsdom (recommended).** Fast, ESM-friendly, good `fetch`/DOM mocking. Cost:
  introduces a Node dev toolchain (`package.json`, `node_modules`, a lockfile) into a
  Python repo, and a separate CI job. Isolate it under `dd/anchor/web_static/` (or a
  top-level `js/`) so it doesn't bleed into the uv workflow.
- **B. Node's built-in `node:test` + a small DOM shim (linkedom/happy-dom).** No test
  framework dep, lighter, but more boilerplate and weaker assertion ergonomics.
- **C. Playwright e2e against a running anchor.** Highest fidelity (real browser, real
  server contract) but heavy, slow, and needs Discord-OAuth wired in CI — better as a thin
  smoke layer *on top of* A/B, not the primary unit layer.

Blocker for all three: `shared.js` currently attaches to `window.*` as classic-script
globals (no `export`). To import functions in a test without a DOM-loaded page, either add
a tiny `export {}` guarded for the test env, or load the file into jsdom and read the
globals off `window`. Decide this when standing the harness up.

## CI

Whichever toolchain: add a **separate** CI job (don't fold JS into the `uv run` Python
lanes). Gate on it the same way `make check` gates Python. Update `CLAUDE.md`'s
"Linting/formatting & type checking" + "CI" sections to document the JS lane, and
`Makefile` with a `test-js` target.

## Open questions

- Is a Node toolchain in this Python repo acceptable, or keep JS test-free and lean on
  manual smoke + type-free vigilance? (This is the real decision — everything else follows.)
- Co-locate tests (`shared.test.js` next to `shared.js`) or a `web_static/tests/` dir?
- Do we also want a Prettier/ESLint pass on the JS while we're adding a Node toolchain
  (there's none today)?
