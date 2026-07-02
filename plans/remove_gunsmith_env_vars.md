# Stub: remove the gunsmith env vars / config leftovers

> **Status: STUB — not started.** The gunsmith *feature* was removed from the bots
> on `dev` (both extension files deleted, `AutoPostSettings.get_gunsmith_enabled` /
> `set_gunsmith` dropped, nav comments de-gunsmith'd — one revertable commit). What
> remains is **config/env-var cleanup only**, deferred because it touches deployment
> config (Railway on dev *and* prod). Re-verify line numbers before acting; the tree
> shifts. Do **not** touch prod without explicit user confirmation.

## Why this is separate

Removing the env vars is decoupled from the code removal because it spans the live
Railway environments (dev + prod), the CI matrix, and `.env-example` — it is not a
pure code change and prod is team-owned. The feature is already gone; these are
harmless-but-dead config values.

## What's left (all gunsmith-specific config)

1. **`dd/common/cfg.py:261`** — `gunsmith_image_url = _getenv("GUNSMITH_IMAGE_URL")`.
   Now unused (its only reader, the deleted anchor extension, set the post image).
   Delete the line. (Left in place for now so the env-var removal lands atomically.)
2. **`.env-example:53`** — `GUNSMITH_IMAGE_URL="https://www.example.com"`. Delete.
3. **`.github/workflows/ci.yml:23`** — `GUNSMITH_IMAGE_URL: "https://example.com/gunsmith.png"`.
   Delete.
4. **`.github/workflows/ci.yml:29`** — the `FOLLOWABLES` JSON still carries a
   `"gunsmith":5` key. Remove just that key; keep the JSON valid. Nothing reads it
   anymore (the beacon nav command + anchor autopost that used `cfg.followables["gunsmith"]`
   are gone), but drop it to keep the fixture honest.
5. **Railway env vars (dev + prod), for both `beacon` and `anchor`:**
   - unset `GUNSMITH_IMAGE_URL`;
   - edit the `FOLLOWABLES` JSON var to drop the `"gunsmith"` key.
   Do prod only after explicit user OK (per CLAUDE.md / memory `deploy-remote-is-shark`).
   A stale `"gunsmith"` key in `FOLLOWABLES` is inert (no code reads it), so this is
   low-urgency housekeeping, not a correctness fix.

## Optional data cleanup (not env vars)

- The `auto_post_settings` table may hold a row with `name = "gunsmith"` (from servers
  that had the autopost enabled). It's orphaned once the feature is gone. Optionally
  `DELETE FROM auto_post_settings WHERE name = 'gunsmith';` on dev then prod (explicit
  OK for prod). Harmless to leave.
- Historical docs still name gunsmith and are intentionally *not* edited here (they're
  point-in-time records): `docs/v2_v3_behavior_audit.md`,
  `docs/decisions/anchor_admin_consolidation_for_user_install.md`,
  `plans/autopost_cv2_migration.md`. Leave as-is unless doing a docs sweep.

## Verification

- `uv run ruff check`, `uv run ty check`, `uv run python -m pytest` (sandbox disabled —
  memory `uv-commands-need-sandbox-disabled`).
- `rg -in gunsmith dd/ .env-example .github` returns nothing.
- CI passes with the trimmed `FOLLOWABLES` (valid JSON, no gunsmith key).
- Bots boot on dev with the Railway vars removed (`FOLLOWABLES` still parses).

## References

- Feature-removal commit on `dev` (this deletion).
- `dd/common/cfg.py` (`followables` from `FOLLOWABLES`, `gunsmith_image_url`).
