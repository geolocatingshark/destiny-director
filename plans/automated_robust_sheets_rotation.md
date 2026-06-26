# Automated, ordering-robust Google Sheets for fixed-cycle post types

> **Status: DEFERRED — possible but unlikely to be implemented.** Worked out in
> full, but judged **too complex / too many moving parts** relative to the benefit.
> The active search is for a *simpler* rotation-data store (see "Simpler
> alternatives" at the end). Kept here for a future agent in case the Sheets
> approach is revived.

## Context

Fixed-cycle Destiny post types that need non-technical editing use Google Sheets
via gspread (`lost_sector` is the prototype: `dd/sector_accounting/`,
`dd/common/lost_sector.py`, `dd/{anchor,beacon}/extensions/lost_sector.py`).
Keeping a real spreadsheet as the editing surface is desirable — the problems are:

1. **Manual per-post-type setup** — create the workbook, lay out worksheets in the
   exact expected order, share with the service account, wire the URL into config,
   every time a new sheet-backed post type is added.
2. **Positional fragility** — the reader breaks if a human reorders tabs/rows/cols:
   - worksheets by index: `get_worksheet(1..4)`
     (`dd/sector_accounting/sector_accounting.py:269-272`),
   - hardcoded column indices (`sector_accounting.py:194-220`;
     `EntityRotation.from_gspread(values, column:int)`,
     `dd/sector_accounting/utils.py:44-59`),
   - positional cross-sheet row-zip with `strict=True`
     (`sector_accounting.py:182-184`).
3. Overall **heavy moving parts**: gspread dep, a service account with 7 `SHEETS_*`
   secrets, Drive+Sheets APIs, and a live Google network call on every post
   (`dd/common/lost_sector.py:92-98` — no caching).

This plan keeps Sheets but removes pains (1) and (2). It does **not** remove the
moving parts in (3) — which is why it's deferred.

## Key enabler

The `sector_accounting` domain objects (`Sector`, `DifficultySpecificSectorData`,
`Rotation`, `EntityRotation`, `SectorData`) and the consumption interface
`Rotation.__call__(date) -> list[Sector]` are source-agnostic. Only the
`from_gspread*` constructors are gspread/positional. So the work is: a name-based
reader + a template-driven provisioner; everything downstream is unchanged.

## Decisions baked in

- Keep gspread + `SHEETS_*` credentials.
- Auto-created Sheets are **service-account-owned and auto-shared** (writer) to
  configured admin email(s) — works with a plain Google account, no Workspace.
- Existing `lost_sector` data is **adopted in place** — no data import/re-entry;
  the cutover is reader-alignment + a parity gate.

## Design

### 1. Template registry — one schema drives provisioning AND reading
`dd/sector_accounting/sheet_templates.py`:
```python
@attr.s
class ColumnSpec:
    field: str                 # internal field, e.g. "void_shields"
    header: str                # exact header text, e.g. "Void"
    validation: list[str] = attr.ib(factory=list)   # optional dropdown enum
@attr.s
class SheetTab:
    title: str                 # resolved by name, not index
    columns: list[ColumnSpec]
    key_field: str | None = None   # name column to join across tabs
@attr.s
class SheetTemplate:
    key: str                   # "lost_sector"
    spreadsheet_title: str
    tabs: list[SheetTab]
    reference_cell: str = "A2" # keeps the existing A2 ref-date convention
```
Author `LOST_SECTOR_TEMPLATE` to match the existing sheet's real tab titles/headers
(via a one-time discovery dump). Registry: `TEMPLATES: dict[str, SheetTemplate]`.
New sheet-backed post type = one entry.

### 2. Provisioning helper — `dd/common/sheet_provisioning.py`
gspread `create(title)` → `add_worksheet(tab.title)` per tab → write header row
(`[c.header for c in tab.columns]`) → `freeze(rows=1)` → optional data-validation
dropdowns via `batch_update` → `del_worksheet(sheet1)` → `share(email,
perm_type="user", role="writer")` for each admin email → return `sh.url`. Blocking
I/O → call via `asyncio.to_thread` (matches `dd/common/lost_sector.py:92`).
Requires the **Google Drive API** enabled on the service account (one-time).

### 3. De-fragilized reader (lands first; fixes existing prod fragility)
Refactor `dd/sector_accounting/sector_accounting.py` + `utils.py` to read via the
template: worksheet-by-title; columns-by-header-name (`{header: index}` from row 1
mapped through `ColumnSpec`); cross-sheet merge by `key_field` (replacing the
positional `zip(..., strict=True)`); missing/extra names → warn + skip. Keep the
A2 `_start_date_from_gspread` logic.

### 4. `RotationSource` table — `dd/common/schemas.py`
`key` PK → `sheet_url`, following the `AutoPostSettings` idiom (`:1303-1407`;
`@ensure_session(db_session)` from `dd/common/utils.py:328-349`). Seed
`lost_sector` from `cfg.sheets_ls_url` (keep cfg as fallback). Atlas migration;
auto-applies at boot (docker-entrypoint runs `atlas migrate apply`).

### 5. Commands & wire-up
New owner-gated anchor group `dd/anchor/extensions/rotation_sheets.py` (owner hook
`dd/common/auth.py:49-59` + control-guild scope from `dd/anchor/__main__.py`):
`/rotation provision <type>` (create + share + store URL), `/rotation link <type>
<url>`, `/rotation url <type>`. The 3 reader call sites source the URL from
`RotationSource` (fallback `cfg`): `dd/common/lost_sector.py:92-98`,
`dd/beacon/extensions/lost_sector.py:73-78`,
`dd/anchor/extensions/lost_sector.py:126`.

## Cutover for existing lost_sector — no manual data entry
1. One-time discovery (dev, with creds): dump the live workbook's tab titles + each
   tab's header row (throwaway gspread script); author `LOST_SECTOR_TEMPLATE` to
   match. 2. Seed `RotationSource[lost_sector]` from `cfg.sheets_ls_url`.
3. **Parity gate:** run the new name-based reader and the current positional reader
   against the live sheet; assert identical `Rotation`/`Sector` output (incl. A2
   reference date) across several dates spanning a reset. 4. Remove the legacy
   positional reader once parity holds. No data-copy command needed.

## Files
- New: `dd/sector_accounting/sheet_templates.py`,
  `dd/common/sheet_provisioning.py`, `dd/anchor/extensions/rotation_sheets.py`.
- Modify: `dd/sector_accounting/sector_accounting.py` + `utils.py`,
  `dd/common/schemas.py`, `dd/common/cfg.py` + `.env-example` (add
  `SHEETS_SHARE_EMAILS`), the 3 lost_sector call sites, `migrations/`.

## Verification
- Unit tests (`dd/sector_accounting/tests/`): reordered tabs / shuffled columns /
  misaligned rows → identical parsed output.
- **Adoption parity gate**: dual-reader diff against the live sheet across a
  rollover (needs `SHEETS_*` creds + Drive API; run uv with the Bash sandbox
  disabled).
- `/rotation provision <type>` integration: workbook created with correct
  tabs/headers, shared, URL stored.
- Output parity: anchor `/lost_sector … show` + beacon `/ls today` (incl.
  lookahead) identical before vs. after.
- `make atlas-migration-plan`; locally `make destroy-schemas && make
  create-schemas`. `uv run ruff check`, `uv run ty check`,
  `uv run python -m pytest`.

## Why deferred / trade-offs
Still keeps gspread + the 7 `SHEETS_*` secrets + the live Google call at post time —
the bulk of the "moving parts" weight. It only removes the manual setup and the
fragility. Service account remains owner of created sheets (native Sheets don't
meaningfully consume Drive quota; switch to a Shared Drive `folder_id` only if that
ever bites).

## Simpler alternatives being explored instead (fewer moving parts)
- **Code constant** (like `dd/beacon/extensions/distortion.py`): zero moving parts;
  dev-only edits + redeploy. Good for never-changing cycles, not the evolving
  lost-sector catalog.
- **Local JSON file in repo** (stdlib `json`; JSON already idiomatic here — no new
  dep): one file + parser, no service account / gspread / secrets / DB / runtime
  network; version-controlled. Editor uses GitHub web → commit → Railway redeploy.
- **Remote JSON fetched at runtime** (GitHub "raw" file or a Gist): bot `GET`s a
  human-readable JSON and `json.loads` it (aiohttp + json already idiomatic). Edited
  in the browser, no redeploy, no service account, no gspread, no secrets, no DB —
  moving parts reduce to one hosted file + one HTTP GET + a parser. Add a small
  cache + last-good fallback. *Current front-runner.*
- **API/manifest derivation**: champion/shield data is derivable from manifest
  definitions, but assembling it + computing the daily rotation is substantial and
  uncertain — trades one complexity for another, not simpler.
