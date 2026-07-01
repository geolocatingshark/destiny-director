# Rollback runbook — `lost_sector` rotation cutover (gspread → DB-JSON)

Recovery steps if the `lost_sector` cutover (see `plans/rotation_json_db_web_editor.md`)
misbehaves. Grounded in the current code (2026-07-01); re-grep symbols before acting.

## Current state (what reads from where)

- Rotation data lives in the DB: `rotation_data` table (`RotationData` in
  `dd/common/schemas.py`), edited via `/rotation edit` (token-auth web form on anchor,
  `dd/anchor/extensions/rotation_editor.py` + `dd/anchor/web.py`). Shipped on dev
  (commit `99c1d25`); **not on prod**.
- **The reader flip is already shipped as fallback logic, not a pending edit.** The
  shared reader `load_rotation` (`dd/common/lost_sector.py:33`) **prefers the DB doc and
  falls back to the live Google Sheet** (`sector_accounting.Rotation.from_gspread_url`,
  `dd/common/lost_sector.py:63`) when the DB row is missing/unusable. Callers already use
  it (`dd/beacon/extensions/lost_sector.py:69`, `dd/common/lost_sector.py:145`, anchor
  `lost_sector`). So there are **no live `from_gspread_url` reader call sites to "switch
  back"** — the real lever is whether the DB row exists / is good.
- The live Google Sheet is present and **untouched** (read-only source, preserved).
  `gspread` is still a dependency.

## ⚠️ Read before you cut over — the plan's "retire gspread" step is a boot-breaker

Do **NOT** remove the `SHEETS_*` env vars or fully `uv remove gspread` as
`plans/…:step 5` literally says:

1. **`cfg.py` reads the sheet config eagerly at import, with no default** —
   `gsheets_credentials = _sheets_credentials("SHEETS_PROJECT_ID", …)` and
   `sheets_ls_url = _getenv("SHEETS_LS_URL")` (`dd/common/cfg.py:~250-258`). A missing
   `SHEETS_*` var makes `cfg` import raise → **both bots crash at boot** (both import
   `cfg`), not just lost_sector.
2. **Xûr shares the same Sheet** — `dd/anchor/extensions/xur.py:~370` calls
   `XurLocations.from_gspread_url(cfg.sheets_ls_url, cfg.gsheets_credentials)`. And
   `/rotation import-from-sheet` itself uses `from_gspread_url`
   (`rotation_editor.py:~326`). Retiring gspread breaks Xûr + the importer.

**Therefore:** for this cutover, "retire gspread" must be scoped to only the
lost_sector-specific fallback branch (and, optionally, later the import command) — **keep
`gspread` + `SHEETS_*` for Xûr** until Xûr is also migrated. The lightest *correct*
cutover is simply "make the DB row good and rely on it"; you don't need to remove
anything. If you do a scoped retire, keep every `SHEETS_*` var set.

## Rollback A — before any gspread retire (the common case): no code, no deploy

Because `load_rotation` auto-falls-back to the Sheet, a bad DB doc is undone by
fixing/removing the row:

1. Fix it in place: `/rotation edit lost_sector` → correct the doc → save. **Or** force
   the Sheet fallback by deleting the row:
   - `railway connect MySQL` (environment **dev**), then:
     `DELETE FROM rotation_data WHERE name = 'lost_sector';`
2. `load_rotation` caches in memory (with a last-known-good fallback), so **restart the
   bots to drop the cached bad doc** and re-read: `railway redeploy -s anchor -y` and
   `railway redeploy -s beacon -y` (env dev), or wait out the cache TTL.
3. Posts now render from the live Sheet again.

## Rollback B — if you already did a (scoped) gspread retire

Only if you removed the lost_sector fallback branch / import command in a commit:

1. `git revert <retire-commit>` (or `git checkout <pre-retire-sha> -- <the files>`).
2. `uv sync` **(Bash sandbox disabled — the uv cache is read-only under the sandbox)**.
3. Redeploy dev (deploy-on-push is **disabled** for dev, so this is required):
   `make deploy-anchor-dev` and `make deploy-beacon-dev` (sandbox disabled).
4. Confirm the `SHEETS_*` vars are still set on Railway dev (they must be — Xûr needs
   them; if you wrongly removed them, re-add and redeploy).

## Do NOT roll back

- **The `rotation_data` table + its Atlas migration** — additive; leave them (dropping
  needs a down-migration and is pointless; an empty/absent row already yields the Sheet
  fallback). Migrations auto-apply at boot.
- **The web editor** (`/rotation edit`, `dd/anchor/web.py`, `rotation_editor.py`) —
  harmless; leave installed.
- **The OAuth persistent-server refactor** (`dd/anchor/web.py` + `oauth.py`) — reverting
  it is unnecessary and would break both the `/rotation edit` editor **and**
  `/bungie login`. It needs `PUBLIC_BASE_URL` / `RAILWAY_PUBLIC_DOMAIN` set on dev anchor.

## Data notes

- The Google Sheet is untouched during cutover → reverting readers/row restores
  correctness immediately.
- **Stale-Sheet surge caveat:** the Sheet's surge column drifted from a clean cycle, but
  `surge` is **never rendered in the post** (only the `/rotation edit` preview + the
  parity check), so it doesn't affect posted output either way. Surges were already
  corrected in the dev DB via the editor.

## Verify after rollback

- Discord: beacon `/ls today` (incl. the lookahead pages) and anchor
  `/lost_sector … show` render the correct sector/graphic.
- Discord: `/bungie login` still completes (OAuth server intact).
- Local (Bash sandbox disabled): `uv run ruff check`, `uv run ty check`,
  `uv run python -m pytest`, and `uv run python -OOm dd.anchor` boots cleanly.
