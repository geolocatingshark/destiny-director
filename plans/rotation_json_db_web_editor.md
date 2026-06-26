# Lost-sector rotation: DB-JSON + token-auth web form on anchor (Case 2, pure-Python)

> **Status: IMPLEMENTED & DEPLOYED TO DEV** (2026-06-26, commit `99c1d25` on dev/
> `shark/dev`). The DB store, schema, `from_json`/`to_json`, persistent web app + OAuth
> refactor, `/rotation edit` editor, and `/rotation import-from-sheet` are all built and
> tested (425 tests green). gspread is **kept as a runtime fallback** (`load_rotation`
> prefers the DB) — step 7 (`uv remove gspread` + `SHEETS_*` deletion) is **deferred**
> until DB parity is confirmed on dev. **Not on prod** (`main`/`shark/main` unchanged).
>
> **Two deviations from the plan below:**
> 1. **Editor UI is a bespoke dependency-free vanilla-JS form**, NOT json-editor: its
>    built `dist` is unreachable through the sandbox's host allowlist (not committed to
>    the repo, no GitHub release assets, npm CDNs not allowlisted). Same UX (checkbox
>    champion/shield pickers, `<datalist>` schedule dropdowns, preview-on-demand, save);
>    server validation via `fastjsonschema` is unchanged. Revisit json-editor only if a
>    CDN is allowlisted or the dist is vendored manually.
> 2. The OAuth refactor kept `refresh_api_tokens(runner=…)` / `get_webserver_runner()`
>    as vestigial (ignored) params so the ~7 autopost call sites stayed byte-identical
>    (lower risk); only `_wait_for_token_from_login` lost its server lifecycle.
>
> **Remaining to cut over (on dev):** run `/rotation import-from-sheet lost_sector` on
> dev anchor → check the parity note + `/rotation edit` preview → then retire gspread.
> Needs `PUBLIC_BASE_URL`/`RAILWAY_PUBLIC_DOMAIN` set on dev anchor for the editor link.
>
> Original direction approved 2026-06-26; the "automate-gspread" design in
> `plans/automated_robust_sheets_rotation.md` is the deferred fallback. See memory
> `rotation-data-store-direction`.

## Context

Replace the Google Sheets + gspread store for `lost_sector` with a JSON document in
the DB, edited through a friendly **token-authenticated web form** served by anchor.
Case 2 = the **Termux-safe / pure-Python** stack (decided): the form is a vendored
browser asset (no Python dep), validation reuses the existing attrs domain objects
(+ optional pure-Python `fastjsonschema`); **no pydantic, no `jsonschema` (Rust)**.
Removes gspread + 7 `SHEETS_*` secrets + the per-post Google call.

**Two findings that shape the build (verified in code):**
1. **Anchor's webserver is transient** — `oauth.py:179-195` (`_wait_for_token_from_login`)
   starts a `TCPSite` on `0.0.0.0:cfg.port` per `/bungie login` and tears it down in
   `finally`. There is **no always-on server**. Both the OAuth callback and the editor
   must live on the one Railway-exposed port, so we introduce **one persistent
   aiohttp app** and fold the OAuth callback into it.
2. **Rendered fields are few** — `format_post` (`dd/common/lost_sector.py:124-147`)
   renders `:LS: [name](shortlink_gfx)` and, if `lost_sector_details` is on,
   `format_data` (champions/shields presence, via `champions_list`/`shields_list`).
   `surge`, `threat`, `overcharged_weapon`, `modifiers` are stored but not rendered;
   `reward`/`legendary_rewards` are never even populated (dead). Keep the stored ones
   for lossless import + the details view, but de-emphasize them in the UI.

## Data model — JSON document (one row per post type)

`sectors` is an **array** (not a name-keyed map) so json-editor renders friendly
add/remove/reorder cards and the schedule can pull a **dropdown of sector names**
from it. Champions/shields are **presence** multi-selects (counts are never rendered;
import maps `count != 0 → present`). `surge_cycle` is per-day lists of elements
(replaces the `,&`-delimited string).

```jsonc
{
  "version": 1,                             // for in-code shape migrations
  "reference_date": "2023-07-20",          // base date; buffer applied at load
  "schedule": {                             // 9 zones, each an independent daily cycle
    "Cosmodrome": ["Exodus Garden 2A", "Veles Labyrinth", "..."],
    "Dreaming City": ["..."], "EDZ": ["..."], "Europa": ["..."], "Moon": ["..."],
    "Neomuna": ["..."], "Nessus": ["..."], "Pale Heart": ["..."], "Throne World": ["..."]
  },
  "surge_cycle": [ ["Solar"], ["Arc","Void"], ["Stasis"] ],   // per-day element lists
  "sectors": [
    {
      "name": "Exodus Garden 2A",
      "shortlink_gfx": "https://kyber3000.com/...",
      "expert": { "champions": ["Barrier"],            "shields": ["Arc"] },
      "master": { "champions": ["Barrier","Overload"], "shields": ["Arc","Void"] },
      "threat": "Arc", "overcharged_weapon": "Auto Rifle",   // advanced (stored, unrendered)
      "expert_modifiers": "", "master_modifiers": ""          // advanced
    }
  ]
}
```

## JSON Schema (draft-07 + json-editor UI keywords)

One schema per type = single source of truth for **form generation** (json-editor)
**and** server validation (`fastjsonschema` ignores the UI-only keywords `options`,
`watch`, `enumSource`, `headerTemplate`, `format`). Schedule items deliberately carry
**no hard `enum`** (names are dynamic) — typo safety is the UI dropdown + a tolerant
loader. Lives in `dd/common/rotation_schema.py` as `LOST_SECTOR_SCHEMA`.

```jsonc
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["reference_date", "schedule", "surge_cycle", "sectors"],
  "properties": {
    "reference_date": { "type": "string", "format": "date", "title": "Rotation start date" },

    "schedule": {
      "type": "object", "title": "Daily schedule (per destination)",
      "required": ["Cosmodrome","Dreaming City","EDZ","Europa","Moon","Neomuna","Nessus","Pale Heart","Throne World"],
      "properties": {
        "Cosmodrome": { "$ref": "#/definitions/zoneCycle" },
        "Dreaming City": { "$ref": "#/definitions/zoneCycle" }
        /* …the other 7 zones, all $ref zoneCycle… */
      },
      "additionalProperties": false
    },

    "surge_cycle": {
      "type": "array", "title": "Surge cycle (per day)",
      "items": {
        "type": "array", "format": "checkbox", "uniqueItems": true,
        "items": { "type": "string", "enum": ["Solar","Arc","Void","Stasis","Strand"] }
      }
    },

    "sectors": {
      "type": "array", "format": "tabs", "title": "Sectors",
      "headerTemplate": "{{ self.name }}",
      "items": {
        "type": "object", "required": ["name","shortlink_gfx","expert","master"],
        "properties": {
          "name": { "type": "string", "title": "Name" },
          "shortlink_gfx": { "type": "string", "format": "uri", "title": "Graphic link" },
          "expert": { "$ref": "#/definitions/difficulty", "title": "Expert" },
          "master": { "$ref": "#/definitions/difficulty", "title": "Master" },
          "threat": { "type": "string", "title": "Threat (advanced)",
                      "enum": ["","Arc","Solar","Void","Stasis","Strand"], "options": {"collapsed": true} },
          "overcharged_weapon": { "type": "string", "title": "Overcharged weapon (advanced)" },
          "expert_modifiers": { "type": "string", "title": "Expert modifiers (advanced)" },
          "master_modifiers": { "type": "string", "title": "Master modifiers (advanced)" }
        }
      }
    }
  },

  "definitions": {
    "zoneCycle": {
      "type": "array", "title": "Daily sectors",
      "items": {
        "type": "string",
        "watch": { "secs": "sectors" },
        "enumSource": [{ "source": "secs", "value": "{{ item.name }}" }]
      }
    },
    "difficulty": {
      "type": "object",
      "properties": {
        "champions": { "type": "array", "format": "checkbox", "uniqueItems": true,
          "items": { "type": "string", "enum": ["Barrier","Overload","Unstoppable"] } },
        "shields": { "type": "array", "format": "checkbox", "uniqueItems": true,
          "items": { "type": "string", "enum": ["Arc","Void","Solar","Stasis","Strand"] } }
      }
    }
  }
}
```

UX wins this gives the editor: per-sector **tabbed cards**, **checkbox** champion/shield
pickers (no magic counts), **dropdown** schedule cells auto-populated from the sector
list (no typos), per-day surge multi-selects, a date picker, and advanced fields
collapsed by default.

## UI — editor page (vendored, no build)

**Layout: single column, preview on demand** (decided) — the form stacks
top-to-bottom; a **Preview** button renders the post inline below it (no live /
debounced updates), and **Save** commits. Simplest build, least JS.

`GET /rotation/edit?type=&token=` serves one HTML page (`dd/anchor/web_static/`)
that inlines the schema, the current data, type and token, and loads a **pinned
vendored `@json-editor/json-editor`** dist JS + one small theme CSS (e.g. Spectre).
JS:
```js
const ed = new JSONEditor(el, {
  schema: SCHEMA, startval: DATA, theme: 'spectre',
  disable_edit_json: true, disable_properties: true, disable_collapse: false,
});
// Preview button -> POST /rotation/preview {token, type, data: ed.getValue()} -> render returned HTML
// Save button    -> errs = ed.validate(); if (!errs.length) POST /rotation/edit {token, type, data}
```

Mockup (single column, preview on demand):
```
┌ Rotation Editor — lost_sector ───────────────────────────┐
│ Authorized via Discord · link expires in 14:32           │
│ Rotation start date [ 2023-07-20 📅 ]                     │
│ ▾ Schedule                                               │
│    ▾ Cosmodrome  1 [Exodus Garden 2A ▾][↑][↓][✕]  [+Day] │
│    ▸ Dreaming City ▸ EDZ ▸ … ▸ Throne World              │
│ ▾ Surge cycle                                            │
│    Day 1 [✓]Solar [ ]Arc [ ]Void [ ]Stasis [ ]Strand    │
│ ▾ Sectors   ┌ Exodus Garden 2A │ Veles… │ + ┐           │
│    │ Name [Exodus Garden 2A]  Graphic [https://…]        │
│    │ Expert  Champ [✓]Barrier [ ]Overload [ ]Unstop      │
│    │         Shield[✓]Arc [ ]Void [ ]Solar [ ]Sta [ ]Str │
│    │ Master  …    ▸ Advanced (threat/overcharge/mods)    │
│    └──────────────────────────────────────────────────── │
│ [👁 Preview]                              [💾 Save]       │
│ ───────────────────────────────────────────────────────  │
│ (Preview renders the real post inline here on demand)    │
└──────────────────────────────────────────────────────────┘
```

- **Preview (on demand):** the Preview button `POST`s the current `ed.getValue()` to
  `/rotation/preview`, which builds `Rotation.from_json` and calls the existing
  `format_post` for today, returning an HTML rendering of the embed
  (title/description/image) shown inline below the form. Emoji: resolve `:name:` to
  real Discord emoji CDN URLs via `fetch_emoji_dict` for fidelity, with a plain-text
  fallback — so the editor sees the real post before saving (the Sheet never offered
  this).
- **Save:** client-side `ed.validate()` blocks malformed input; server re-validates
  (fastjsonschema + the hard gate below) before writing.

Pure-Python alt (if we ever want zero vendored JS): server-rendered `jinja2` +
`WTForms` + vendored `htmx`. Not chosen — json-editor gives more UX for less code and
is browser-only.

## Server architecture — one persistent aiohttp app on anchor

New `dd/anchor/web.py`: build a single `aiohttp.web.Application` with routes
`/oauth/callback` (moved from `oauth.py:webserver_runner_preparation`) **and**
`/rotation/edit`, `/rotation/preview`. Start a `TCPSite` on `0.0.0.0:cfg.port` once,
in a `StartedEvent` listener; stop on `StoppingEvent`. Wire from `dd/anchor/__main__.py`.

**OAuth refactor (contained, keep behavior identical):** the `/oauth/callback`
handler already sets the token via `OAuthStateManager.set_access_token` — unchanged.
`_wait_for_token_from_login` drops its `runner.setup()/TCPSite/finally-cleanup`
(`oauth.py:184-195`) and becomes just the poll-with-timeout loop; `/bungie login`
(`bungie_api/__init__.py`) stops calling `get_webserver_runner()`. Net: removes the
transient-server lifecycle (and its "leaked webserver" caveat). Validate `/bungie
login` end-to-end after.

## Auth — Discord-minted random URL (reuse the OAuth idiom)

- `RotationEditTokenManager` mirroring `OAuthStateManager` (`oauth.py:25-91`):
  in-memory dict `token -> (type, expiry)`, UUID4, ~15-min expiry, multi-use during
  the window (GET/preview/save), **burned on successful save**. In-memory is fine —
  the web app runs in the anchor process that mints the tokens.
- `/rotation edit <type>` slash command (anchor), owner-gated by the existing owner
  hook (`dd/common/auth.py:49-59`) + control-guild scope (`dd/anchor/__main__.py`):
  mint token → **ephemeral** reply with `{public_base_url}/rotation/edit?type=…&token=…`.
- Public URL: add `cfg.public_base_url` from `RAILWAY_PUBLIC_DOMAIN` (Railway-provided)
  with an explicit `PUBLIC_BASE_URL` override; add to `.env-example`.

## Storage choice — JSON column in the DB (decided)

`lost_sector` data is read by **both** anchor and beacon (separate Railway services),
so the source of truth must be shared infra = the MySQL DB (already a hard dep). That
rules out local JSON files (disk isn't shared between services; the only shared file
variant is a repo file → redeploy-to-edit, which kills the live editor). A **full
relational schema** is over-engineered: the app always loads the whole rotation and
computes in Python (`Rotation.__call__`), edits are whole-document and seasonal, and
nothing queries inside the data — so its wins (FKs, in-data SQL, partial updates) are
unused, while its costs (4–5 tables + a multi-table reconciliation on every save + a
migration per shape change + per-type table explosion for heterogeneous future types)
are recurring. **JSON column** is the fit: shared across both bots, exact match for
whole-doc read-mostly access, **one table with zero future migrations** (new type =
new row), heterogeneous-type-friendly; integrity is enforced at the app layer (schema
+ attrs construction + UI dropdowns) plus the tolerant loader. Aligns with the repo's
"DB config table for runtime config" stance (`redis-evaluated-and-rejected`).

## DB store

`RotationData(name VARCHAR(32) PK, data JSON, updated_at DateTime)` in
`dd/common/schemas.py`, following the `AutoPostSettings` idiom (`:1303-1407`,
`@ensure_session(db_session)`): `get_data(name)` / `set_data(name, data)`. Atlas
migration; auto-applies at boot (memory `migrations-auto-apply-at-boot`).

**The PK (`name`) is the post-type slug** — one row per post type, value e.g.
`"lost_sector"` (future: `"dares_of_eternity"`, `"ascendant_challenge"`). It reuses
the existing slug convention (`AutoPostSettings.name` / `cfg.followables` keys) so a
post type is addressed identically across its enabled-flag, channel, and data; it's
the join key for `/rotation edit <type>`, `load_rotation()`, and the schema registry.
It is **not** secret and **not** the editor auth token (tokens live in-memory in
`RotationEditTokenManager`, never in the DB). Each row's `data` is the full JSON doc
(with a `version` field for in-code shape migrations).

## Reader / writer (sector_accounting, DB-agnostic)

- `Rotation.from_json(doc, buffer=...) -> Rotation` (pure): `start_date` =
  `date(reference_date)` + `timedelta(hours=16, minutes=60-buffer)` (mirror
  `_start_date_from_gspread`, `sector_accounting.py:311-330`); `sector_rot` =
  `{zone: EntityRotation(list)}`; `surge_rot` = `EntityRotation([" & ".join(day) …])`;
  `sector_data` from the `sectors` array (champions/shields lists → the count fields,
  present→-1 else 0). **Tolerant:** a schedule name absent from `sectors` is treated
  like today's "TBC" `KeyError` path (`beacon/.../lost_sector.py:85-94`).
- `Rotation.to_json()` (for the one-shot import): inverse mapping
  (`champions_list`/`shields_list` → presence arrays; surge string split on `,&`).
- `dd/common/lost_sector.py`: `async load_rotation(buffer) -> Rotation` reads
  `RotationData["lost_sector"]` with an in-memory cache + last-known-good fallback,
  then `Rotation.from_json`. Swap the 3 call sites off `from_gspread_url`:
  `dd/common/lost_sector.py:92-98`, `dd/beacon/extensions/lost_sector.py:73-78`,
  `dd/anchor/extensions/lost_sector.py:126`.

## Cutover — no manual data entry

1. Ship everything above with gspread still present.
2. **One-shot import:** `/rotation import-from-sheet lost_sector` (owner) reads the
   live Sheet via the existing `from_gspread_url`, `Rotation.to_json()`, `set_data`.
3. **Parity gate:** assert `Rotation.from_json(doc)` yields identical
   `Rotation`/`Sector` output to the live Sheet reader across dates spanning a reset.
4. Flip the 3 call sites to `load_rotation`.
5. **Retire gspread:** `uv remove gspread`; delete `from_gspread*`/
   `SpreadsheetBackedData` + gspread bits of `dd/sector_accounting/utils.py`; remove
   `SHEETS_*` + `gsheets_credentials` + `sheets_ls_url` from `cfg.py` + `.env-example`.

## Files
- **New:** `dd/anchor/web.py` (persistent app + lifecycle); `dd/anchor/extensions/rotation_editor.py`
  (token mgr, slash cmd, route handlers); `dd/anchor/web_static/` (pinned json-editor
  JS + theme CSS + editor.html); `dd/common/rotation_schema.py` (schema + registry).
- **Modify:** `dd/common/schemas.py` (+`RotationData`);
  `dd/sector_accounting/sector_accounting.py` (+`from_json`/`to_json`; later drop gspread)
  + `utils.py`; `dd/common/lost_sector.py` (+`load_rotation`, swap);
  `dd/beacon/extensions/lost_sector.py` + `dd/anchor/extensions/lost_sector.py` (swap;
  register editor); `dd/anchor/extensions/bungie_api/oauth.py` + `__init__.py` (OAuth
  refactor); `dd/anchor/__main__.py` (start web app); `dd/common/cfg.py` + `.env-example`
  (+`PUBLIC_BASE_URL`; later drop `SHEETS_*`); `pyproject.toml`/`uv.lock`; `migrations/`.

## Dependencies
- **+ `fastjsonschema`** (pure Python, no deps) — optional but recommended; Termux-safe.
- **Vendored** `@json-editor/json-editor` JS + CSS — browser asset, **not** a Python
  dep, zero Termux impact. Pin a copy (no runtime CDN).
- **− `gspread`** at cutover. Net Python deps: +1 small, −1 larger.

## Verification
- **Unit** (`dd/sector_accounting/tests/`): `from_json`/`to_json` round-trip;
  malformed docs rejected; schedule name not in `sectors` → no-data (not a crash).
- **Import parity gate:** imported JSON parses identically to the live Sheet across a
  reset (needs `SHEETS_*` for the one-shot; run uv with the Bash sandbox **disabled**
  — memory `uv-commands-need-sandbox-disabled`).
- **Editor:** `/rotation edit` → open URL → tabbed form prefilled → dropdowns/checkboxes
  work → **preview** matches the real post → save → DB updated; reused-after-save /
  expired / absent token rejected; routes 401 without a valid token.
- **OAuth regression:** `/bungie login` still completes on the persistent server.
- **Output parity:** anchor `/lost_sector … show` + beacon `/ls today` (incl.
  lookahead) identical before vs. after.
- **Schema/lint/types:** `make atlas-migration-plan`; locally `make destroy-schemas &&
  make create-schemas`; `uv run ruff check`, `uv run ty check`,
  `uv run python -m pytest` (sandbox disabled); run `uv run python -OOm dd.anchor`.

## Sequencing & risk
1. `RotationData` + migration → 2. `from_json`/`to_json` + unit tests → 3. persistent
web app + **OAuth refactor** (riskiest; verify login) → 4. token mgr + `/rotation edit`
+ editor page + preview → 5. one-shot import + parity gate → 6. flip readers → 7. retire
gspread. Each step is independently shippable; gspread stays the live source until step 6.
