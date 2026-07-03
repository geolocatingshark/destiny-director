# Plan (STUB): retire gspread / Google Sheets entirely — Phase 2

> **Status: DEFERRED stub.** Do this **only after** BOTH DB-JSON migrations are live and
> parity-confirmed: **lost_sector** (`plans/rotation_json_db_web_editor.md`, its step 7) and
> **Xur locations** (`plans/xur_location_db_json.md`). Until both read from the DB, gspread is
> still the runtime fallback — removing it early breaks the fallback path.

## Context

One shared Google Sheet (`SHEETS_LS_URL`, read with the `SHEETS_*` service account) backs two
features today: **lost_sector** (worksheets 1–4) and **Xur locations** (worksheet 7), plus a
**dormant** Xur armor-sets map (`XurArmorSets`, worksheet 6 — already commented out in
`dd/anchor/extensions/xur.py`). Once both live consumers read from the DB-JSON store, nothing
reads the sheet → remove the entire gspread stack + its config/secrets.

## Preconditions (verify before starting)
- `load_rotation` (`dd/common/lost_sector.py`) is serving from the DB and the gspread fallback
  is no longer relied on (lost_sector row seeded + parity confirmed on prod).
- `load_xur_locations` (`dd/anchor/extensions/xur.py`) same (Xur row seeded + parity confirmed).
- `rg -n "gspread|from_gspread|sheets_ls_url|SHEETS_|service_account" dd/` shows **only** the
  code slated for deletion below (no missed consumer).

## Work
- `uv remove gspread` + `uv sync` (sandbox off).
- Delete the gspread readers:
  - `dd/sector_accounting/sector_accounting.py` — `SpreadsheetBackedData.from_gspread_url`,
    `Rotation.from_gspread`, `_start_date_from_gspread`, `SectorData`'s gspread constructor +
    `gspread_data_row_to_sector`, the `import gspread`.
  - `dd/sector_accounting/utils.py` — `all_values_from_sheet`, `EntityRotation.from_gspread`.
  - `dd/sector_accounting/xur.py` — `XurLocations.from_gspread`; **decide on `XurArmorSets`**
    (ws6, already unused) — delete it too unless there's a plan to revive it.
- Drop the gspread **fallback branches** from `load_rotation` and `load_xur_locations` — leave
  DB + last-known-good cache only.
- Remove the one-shot `import_from_sheet` paths (lost_sector + `/xur locations import_from_sheet`)
  — no sheet left to import from.
- Config: remove `sheets_ls_url`, `gsheets_credentials`, the `_sheets_credentials` helper and the
  `SHEETS_*` / `SHEETS_LS_URL` reads from `dd/common/cfg.py`; drop those vars from `.env-example`.
- **Manual (user):** delete the `SHEETS_*` variables from the Railway dev **and** prod envs after
  deploy (they become unused). The `MYSQL_URL` etc. stay.
- Update memories: `rotation-data-store-direction`, `rotation-json-store-implemented`.

## Verification (dev, then prod with explicit OK)
- `rg -n "gspread|from_gspread|sheets_ls_url|SHEETS_|service_account" dd/` → nothing.
- `uv run ruff check`, `uv run ty check`, `uv run python -m pytest` (sandbox off).
- Dev: a Xur autopost + a lost_sector autopost + `/xur` + `/ls today` render correctly from the
  DB with gspread gone; both bots start clean. `make deploy-*-dev`.
- Net: pyproject loses `gspread` (and its transitive deps); the `SHEETS_*` secrets can be deleted
  from Railway.
