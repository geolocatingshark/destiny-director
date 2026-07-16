# Follow-up: share the anchor web-form client between weekly_reset & trials

## Context / why

The Trials hybrid post (merged to dev `39f57f9`, 2026-07-16) deduped the **server** side of
the two web-form producers into `dd/anchor/hybrid_post_core.py` (spec-driven route handlers,
publish path, DraftMeta, preview renderer, weapon resolver). The **client** side and the
manifest weapon-pool build were left duplicated. This plan captures the three sharing
opportunities a code review surfaced, with enough detail to execute without re-discovery.

Both producers now share the same server contract (so the client can be shared too):
- `POST /{weekly_reset,trials}/create` and `/edit` take `{...form, publish: bool}` and
  return `{ok, note, warnings, post_this_period, crossposted}` (409 on create-when-current /
  edit-when-absent; 422 problems on publish-validate; 502 `{problems}` on Discord failure).
- `/preview` returns **safe HTML** (escaped, tag-whitelisted); `/delete` → `{ok}`;
  `/auto` → `{enabled}`.
- GET form bootstrap includes `post_this_period` + `crossposted` (drives button visibility).
This contract lives in `hybrid_post_core.post_action/form_get/preview/delete/auto`.

Priority: **A (CSS)** quick + low-risk → do anytime. **B (JS lifecycle)** the real
maintainability win → own PR. **C (weapon pool)** minor efficiency → opportunistic.

---

## A. CSS → `shared.css` (low effort, low risk)

`dd/anchor/web_static/trials_form.css` (136 lines) and `weekly_reset_form.css` (~151) are
~90% byte-identical; both already `<link>` `shared.css` (base reset/theme + `.backlink`/
`.nav`).

**Identical blocks to lift into `shared.css`** (verified byte-identical between the two):
- Page layout: `body { max-width: 1180px }`, `main.layout` grid + the `@media (min-width:
  1000px)` sticky-preview rule, `#form`, `fieldset`/`legend`, `.field`/`.field > label`,
  input/textarea, `label.inline`.
- Preview pane: `.preview-col`, `.preview-head`, `#previewBox` (incl. the `--accent` left
  bar), `#previewBox .emoji`, `.md-h1`, `.md-small`, `#previewBox a`, `.post-image`.
- `#problems`, the sticky `.toolbar`, `button`/`.secondary`/`.danger`/`.tiny`, `.grow`,
  `#status.ok/.err`.
- The **entire Tom-Select dark-theme block** (`.ts-wrapper`, `.ts-control`, `.ts-dropdown`,
  multi-select chips, `clear_button`) — ~40 lines, identical.

**Keep per-form (unique):**
- trials: `#previewBox .md-h3`, `#previewBox .md-bullet::before` (Trials' `### `/`- ` lines).
- weekly_reset: `.checks`, `#conquests`, `.ts-wrapper .clear-button` (its single-select
  widgets) — note the shared block already covers multi chips.

**Risk / verify:** `shared.css` is also loaded by the rotation editor (`rotation_editor.py`)
and the control panel (`control_panel.py` → `control_panel.html`). The moved rules are
scoped by ids/classes those pages don't use (`#previewBox`, `.ts-*`, `.toolbar`, `.md-*`,
form `fieldset`) — but eyeball those two pages after moving. Result: each form CSS shrinks
to only its `.md-h3`/`.md-bullet` (trials) or conquest widgets (weekly_reset).

---

## B. Form-lifecycle JS → `shared.js` (moderate effort/risk — own PR + manual smoke)

`dd/anchor/web_static/trials_form.js` (260) and `weekly_reset_form.js` (~391) share ~150
lines that differ ONLY by the route prefix (`/trials` vs `/weekly_reset`) and two
delete-confirm / status strings. `shared.js` already exists (holds `api()`), vanilla JS, no
build step — the right home.

**Shareable (near-verbatim between the two):**
- `$`/`el` DOM helpers (already duplicated), `setStatus`, `showProblems`.
- Preview: `schedulePreview`/`renderPreview` (debounced ~400ms; `innerHTML` on ok since the
  server returns safe HTML, `textContent` on failure), the `form` submit/`input` listeners,
  `refreshBtn`.
- **`updateButtons()`** — visibility from `postThisPeriod`/`crossposted`: Create +
  Create&publish shown when `!postThisPeriod`; Edit/Delete when `postThisPeriod`;
  Edit&publish when `postThisPeriod && !crossposted`.
- **`postAction(path, publish, okMsg)`** — POSTs `{...readForm(), publish}` to
  `/<prefix>/<path>`; on `data.problems`→showProblems; else re-sync `postThisPeriod`/
  `crossposted` from the response.
- The four button handlers (create, create&publish, edit, edit&publish), the delete handler
  (confirm wording differs), the autopost toggle, and the initial `renderPreview()`.

**Per-producer (stays in each form.js):** the `BOOT` destructuring; **widget construction**
(weekly_reset: weapon pickers + GM strike + crucible modes + raid/dungeon/pantheon selects +
conquest multis; trials: maps textarea + focus-pool Tom-Select multi); and **`readForm()`**
(the payload shapes differ — weekly_reset's many fields vs trials' `maps_text`/`focus_pool`/
`notes_text`/`image_url`).

**Suggested shape:** a `window.initPostForm({ routePrefix, readForm, labels: {deleteDraft,
deletePublished} })` in `shared.js` that wires `updateButtons`/`postAction`/preview/delete/
autopost against `BOOT` + the element ids (both forms already use the SAME ids: `createBtn`,
`createPublishBtn`, `editBtn`, `editPublishBtn`, `deleteBtn`, `previewBox`, `problems`,
`status`, `refreshBtn`, `autopost`, `form`). Each form.js builds its widgets, defines
`readForm`, then calls `initPostForm(...)`.

**Risk:** touches the working weekly_reset form. No unit tests cover the JS — **manually
smoke both forms** after: button visibility across the `post_this_period × crossposted`
states, create/edit ± publish, delete (draft vs published wording), autopost toggle, live
preview. Needs the anchor web UI reachable (Discord-OAuth configured).

---

## C. Manifest weapon pool built twice at startup (minor efficiency — opportunistic)

`weekly_reset._build_indexes`/`get_indexes` and `trials._build_weapon_items`/
`get_weapon_items` each `api._get_latest_manifest(...)`, open their own `aiosqlite`
connection, and run `hybrid_post_core.iter_weapon_items(cur)` — a full scan + JSON-parse of
`DestinyInventoryItemDefinition` (~4166 rows). Both prewarm on `StartedEvent` and cache in
**separate** globals (`weekly_reset._indexes.items`, `trials._weapon_items`); the weapon
tuples are identical → the item table is scanned + decoded twice and held in two copies.

**Approach:** add a cached, process-wide `hybrid_post_core.get_weapon_pool()` (opens the
manifest, returns `iter_weapon_items`). Trials' `get_weapon_items` becomes a thin call to it.
weekly_reset's `_build_indexes` gets its `items` from it (still opens its own connection for
the activity/conquest `SELECT`s, which are weekly_reset-specific and not shareable). Removes
the 2nd full item scan + the duplicate in-memory copy. Startup-only + cached, so low urgency.

---

## Not a sharing issue (noted, no action needed)

- **`reset_ts` / "Live until" trusts the editable boundary field** — this is a *shared
  behavior*, not duplicated code: both `weekly_reset` ("Resets:") and `trials` ("Live until")
  render `next_reset_ts(ctx.reset_ts)` from the form's editable `resetAt`. Consistent, no
  drift risk. If desired, a small UX guard (warn when `resetAt` isn't a Tuesday 17:00
  boundary) would be a feature tweak in each form, not dedup.

## Coordination note

The **gspread-removal** work (branch `rotation/remove-gspread`) is in flight. It touches
lost_sector/xur/sector_accounting/cfg (sheet plumbing); this sharing work touches only
`dd/anchor/web_static/*` and `hybrid_post_core`/producers — **no file overlap** with gspread
removal, so these can proceed independently.
