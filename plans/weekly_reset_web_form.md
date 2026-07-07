# Weekly Reset web form — replace the slash-command input with an owner web page

> **Status: PLANNED, not yet implemented (2026-07-07).** Extends the shipped
> `feat/weekly-reset-autopost` feature. Re-verify symbols + line numbers before executing
> (grep by name — these modules shift under you).

**Provenance.** Built from two read-only codebase surveys (the anchor web app + the
weekly-reset backend), plus an advisory consult (Fable 5) on stack choice. The weekly-reset
input has grown into a sprawl of `set_*` slash commands + a modal + component menus; this
plan swaps the *input UI* to a single owner-authenticated web form, reusing the existing
`rotation_editor.py` web pattern almost verbatim. The data/render/validation core is already
UI-agnostic, so this is a front-end swap over the same persisted store — **not** a stack
switch.

## 0. Structural decisions

1. **Stay vanilla — no JS framework, no bundler, no jinja, no npm.** The page is one state
   object POSTed as JSON; native `<select>`/`<input type=checkbox>`/`<input
   type=datetime-local>` + `FormData` cover it. Adding a JS toolchain to a uv-managed Python
   repo for one page is a permanent tax (second lockfile, Railway build step, CI, split
   skillset) and would leave two paradigms since editor #1 stays vanilla. Consistency with
   the existing 485-line `editor.html` (auth flow, bootstrap-injection, fetch/save) is an
   asset to preserve.
2. **Ship option lists in the bootstrap; filter client-side.** The weapon list is ~4166
   items (`_Indexes.items`); at owner-only scale that's a trivial payload. Inject it (plus
   the per-tier conquest lists, strikes, and the bounded raid/dungeon/pantheon/crucible
   constants) into the page's bootstrap JSON and filter on keystroke — **no autocomplete
   round-trip endpoints needed.**
3. **One vendored no-build widget library: Tom Select.** The only non-trivial widgets are the
   weapon typeahead and the four conquest multi-selects. Vendor `tom-select` (one JS + one
   CSS file, committed under `web_static/vendor/`, referenced by `<script src>`/`<link>` — no
   CDN, no npm) to get both. Everything else is native HTML.
4. **Split the static assets into `.html` + `.css` + `.js`** (see §2a) and land that refactor
   **on `dev` first**, since it touches the *existing* rotation editor infra that both pages
   share. Then rebase the feature branch onto it. Rationale in §6.
5. **The web form and the Discord editor write the same `RotationData` store.** They stay
   coherent by construction; keep `/weekly_reset draft` (seed) and the autopost cron; retire
   the `set_*` command pile (or keep one or two as mobile shortcuts — see §3).

## 1. What already exists (reuse map)

| Layer | Status |
|---|---|
| aiohttp app + lifecycle + Railway public URL | ✅ `web.py` (`start`/`register_routes`), `cfg.public_base_url` |
| Owner-auth: HMAC token → DM link → httpOnly cookie + CSRF | ✅ copy `RotationSessionManager`, `_set_session_cookie`, `_origin_ok` (`rotation_editor.py:72-160`) |
| Data model + persistence | ✅ `WeeklyResetContext.to_dict/from_dict`, `load/save_draft`, `load/save_meta` (same `RotationData` store) |
| Draft seed + rotator/reset math | ✅ `build_draft_context`, `current/next_reset_ts`, `compute_rotator` |
| Field mutators | ✅ all `apply_*` (pure sync) |
| Option lists | ✅ `get_indexes()` → `_Indexes.items` / `.conquests` / `.activities["strike"]`; constants `RAIDS`/`DUNGEONS`/`PANTHEON_BOSSES`/`CRUCIBLE_MODES`/`CONQUEST_TIERS` |
| Preview + validation | ✅ `build_body(ctx)` (pure string), `validate_post(ctx)` (pure) |
| **Publish** | ⚠️ needs `bot`; currently **inlined in `on_confirm` (`weekly_reset.py:~1332-1367`)** → must be extracted (§2e) |
| Static-file serving | ⚠️ none today (`editor.html` is `Path.read_text()` + `.replace()`) → add in §2a |

Everything the form needs to **build, validate, and preview** a draft is already Discord-free.

## 2. Recommended architecture (mirror `rotation_editor.py`)

### 2a. Phase 0 (on `dev`): static serving + split `editor.html`

- Add static serving to the shared app: in `web.py`'s `start()` (or a registrar),
  `app.router.add_static("/static/", WEB_STATIC_DIR)` — one line, serves `web_static/`.
- Split `web_static/editor.html` → `editor.html` (markup + bootstrap marker) + `editor.css` +
  `editor.js`, referenced via `<link rel=stylesheet href="/static/editor.css">` and
  `<script src="/static/editor.js" defer>`. Keep the `/*__BOOTSTRAP__*/ null` injection.
- Extract the genuinely shared bits into `shared.css` + `shared.js` (the `api(path, body)`
  fetch helper, cookie/session-error handling, base styles) so the weekly-reset page reuses
  them.
- Vendor Tom Select into `web_static/vendor/` (committed).
- **Verify the rotation editor still renders + saves + previews unchanged** (it's live dev
  infra — do not break it). Its test is `dd/anchor/tests/test_rotation_editor.py`.

### 2b. Auth — copy the session manager

New `WeeklyResetSessionManager` cloned from `RotationSessionManager` (`rotation_editor.py:72`):
same HMAC-signed, bot-token-keyed, ~2h TTL token; cookie name `weekly_reset_session`, path
`/weekly_reset`. Reuse `_origin_ok` for CSRF. Trust is bootstrapped by an owner-only
`/weekly_reset edit_web` command that mints the token and DMs
`{cfg.public_base_url}/weekly_reset?token=…` (mirror `rotation_editor.py:421-450`); the entry
handler swaps `?token=` for the cookie and redirects to strip it from history.

### 2c. Routes (`register_weekly_reset_routes`, registered at import via `web.register_routes`)

- `GET /weekly_reset` — cookie exchange → serve `weekly_reset_form.html` with the bootstrap:
  `{"draft": load_draft()?.to_dict() or build_draft_context().to_dict(), "options": {...from get_indexes() + constants...}}`.
- `POST /weekly_reset/save` — Origin check → `WeeklyResetContext.from_dict(body)` →
  `validate_post` → `save_draft` (+ `save_meta` status/last-edited). Return problems as JSON.
- `POST /weekly_reset/preview` — Origin check → `build_body(from_dict(body))` → return HTML
  (server-rendered plain preview; `:emoji:` shown as text — matches the rotation-editor
  precedent; exact-emoji preview is a later option via `format_weekly_reset`).
- `POST /weekly_reset/publish` — Origin check → `publish_draft(bot, ctx, meta)` (§2e).

### 2d. Frontend (`weekly_reset_form.html` + `.css` + `.js`)

- **Sections** (one `<fieldset>` each, matching `build_body` order): Reset date
  (`datetime-local`), Updates & Events (update label+url, Iron Banner / Trials toggles,
  events narrative), Vanguard weapons (3 Tom Select weapon pickers + GM strike), **Conquests
  (4 Tom Select multi-selects**, options = `options.conquests[tier]`, values are clean base
  names), Raids/Dungeons (seasonal + 2 rotator `<select>`s each), Pantheon (2 `<select>`),
  Zavala weapon (Tom Select), Crucible (2 mode pickers), Notes/Links, Image URL.
- **Weapon pickers:** Tom Select fed the full `options.items` list; value = item hash (so the
  light.gg deep link resolves), label = `name — type · rarity`. Client-side filter, no server
  round-trip.
- **JS shape** (small pure functions): `readForm() -> draftDict`, `renderPreview(html)`,
  `api(path, body)` (from `shared.js`). **Debounce the `/preview` POST ~400 ms.** Save/Publish
  buttons; show `validate_post` problems inline.
- **Escaping:** bootstrap via `json.dumps(...)` into the `/*__BOOTSTRAP__*/` marker, guarding
  `</script>` (reuse the rotation editor's exact approach).

### 2e. Backend refactor — extract `publish_draft`

Pull the publish body out of `on_confirm` (`weekly_reset.py:~1332-1367`) into:

```python
async def publish_draft(bot: CachedFetchBot, ctx: WeeklyResetContext, meta: DraftMeta) -> DraftMeta:
    """Emoji-substitute, (re)publish to cfg.followables['weekly_reset'] (crosspost),
    record, and update meta. Shared by the Discord editor and the web route."""
```

It calls `format_weekly_reset(ctx, bot)` (emoji sub), then `utils.send_message(..., crosspost=True)`
for first publish or `bot.rest.edit_message(...)` for a republish, then `record_publish` +
`save_meta`. `on_confirm` becomes a thin caller. The web `/publish` route calls the same
helper with the live `bot` (same process). This is the **only** non-mechanical backend change.

## 3. Phased rollout

- **Phase 0 (dev):** static serving + `editor.html` split + `shared.*` + vendor Tom Select +
  verify rotation editor. Land on `dev`; rebase the feature branch (see §6).
- **Phase 1:** `publish_draft` extraction + a unit test that the Discord publish path is
  unchanged. (Pure refactor; ship-able alone.)
- **Phase 2:** auth (`WeeklyResetSessionManager`) + `/weekly_reset edit_web` mint command +
  `GET /weekly_reset` serving a form that round-trips draft state (save only, no publish yet).
- **Phase 3:** the full form UI (Tom Select pickers, conquest multi-selects, preview pane,
  client-side filtering) + `/preview`.
- **Phase 4:** `/publish` wired to `publish_draft`; retire the `set_*` command pile (keep
  `/weekly_reset draft`, `edit_web`, and the autopost cron). Decide whether to keep 1–2
  `set_*` as mobile shortcuts.

## 4. Risks

- **Don't break the live rotation editor** during the Phase 0 split. ⚠️ It's shared dev infra;
  verify render+save+preview and run `test_rotation_editor.py` before merging to dev.
- **Publish coupling.** `publish_draft` must reproduce `on_confirm`'s exact behaviour
  (first-publish crosspost vs republish edit; owner ping; records). Extract carefully; keep
  the Discord path calling it so both stay in sync.
- **Auth is token-possession, not per-user login** (matches the rotation editor). Fine for a
  small owner set; the DM link is the authz. Note it, don't "fix" it.
- **Bootstrap size / escaping.** ~4166 items is fine, but escape via `json.dumps` + `</script>`
  guard to avoid breakout. Owner-only page, so payload size is a non-issue.
- **Tom Select vendored, not CDN** — commit the files; a runtime CDN pull would break the
  offline/owner-only trust model and add an external dependency.
- **Two input paths** (web + any remaining `set_*`) both write `RotationData`; that's coherent
  by design, but document that the web form is the primary path.

## 5. Key files

- **On `dev` (Phase 0):** `dd/anchor/web.py` (static route); `dd/anchor/web_static/editor.html`
  → `editor.html` + `editor.css` + `editor.js`; new `shared.css`/`shared.js`;
  `web_static/vendor/tom-select.*`; possibly a small tweak to `rotation_editor.py` for the new
  asset references.
- **On the feature branch:** `dd/anchor/extensions/weekly_reset.py` (`publish_draft` extraction;
  `WeeklyResetSessionManager`; `register_weekly_reset_routes`; `/weekly_reset edit_web`
  command); new `dd/anchor/web_static/weekly_reset_form.html` + `.css` + `.js`;
  `dd/anchor/tests/test_weekly_reset.py` (publish-refactor + bootstrap/serialisation tests).

## 6. Recommendation: do the split on `dev` via a separate agent, then rebase — YES

The `editor.html` split + static-serving is a **pure refactor of existing `dev` infrastructure**
(confirmed: `web.py`/`rotation_editor.py`/`web_static/*` are identical on `dev` and the feature
branch), and it's a **shared dependency** of the new form (both need the `/static/` route,
`shared.css`/`shared.js`, and the vendored Tom Select). Landing it independently on `dev`:

- keeps it a small, self-contained, reviewable change that isn't gated behind the larger
  weekly-reset PR;
- establishes the static-asset pattern the weekly-reset form builds on, so the feature branch
  starts from the finished infra after a rebase;
- shrinks the (already large) feature PR;
- benefits any future editor page immediately.

**Caveat:** it must be verified against the live rotation editor before merging to dev (render
+ save + preview + `test_rotation_editor.py`) — it's live infra. Workflow: a dedicated agent
does Phase 0 on a `dev`-based branch → verify → merge to `dev` → `git rebase dev` the feature
branch (same clean rebase we did before) → continue Phases 1-4 here.

## Verification

- Phase 0: drive the rotation editor end-to-end (mint link on dev, open, edit, save, preview);
  `make check` incl. `test_rotation_editor.py`.
- Phases 1-4: `make check`; a `publish_draft` unit/behaviour test; then on dev, `/weekly_reset
  edit_web` → open the form → set every section (incl. the 4 conquest multi-selects and a
  weapon typeahead) → preview matches `build_body` → save → `/weekly_reset show` reflects it →
  publish → confirm the crossposted post + records entry. Re-run the existing conquest/render
  tests to ensure the `publish_draft` extraction didn't change Discord output.
