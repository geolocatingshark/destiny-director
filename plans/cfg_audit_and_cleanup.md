# cfg cleanup, env-handling fixes & admins→owner migration

> **Precondition: do NOT start until the v2→v3 (lightbulb) migration on
> `feature-lightbulb-v3` is merged/complete.** This is deferred cleanup, not part of
> the migration. A fresh agent should action it afterward.

## Context

`dd/common/cfg.py` is the single centralized config module for both bots
(`dd.beacon`, `dd.anchor`) and shared code. An audit found dead config, `.env-example`
drift, env-backed knobs that never need per-deploy overrides, a latent boolean-parsing
bug, and a redundant home-grown admin list (`cfg.admins`) that duplicates the existing
bot-owner authorization primitives. This plan removes the cruft, hardens env parsing,
and collapses `cfg.admins` into the owner hook.

Centralization is already airtight: **zero** `os.getenv`/`os.environ` accesses exist
outside `cfg.py` (verified). Keep that property.

---

## 1. Delete dead config — `dd/common/cfg.py`

Both are plain in-module constants with no references anywhere. Safe to delete:
- `kyber_ls_thumbnail` (lines 195-197)
- `reset_time_tolerance` (line 263)

Verify before/after: `rg -n 'kyber_ls_thumbnail|reset_time_tolerance' dd/` → nothing.

## 2. Add `_getbool` helper + fix the SSL bug — `dd/common/cfg.py`

Boolean parsing is currently ad-hoc and inconsistent:
- `disable_bad_channels` = `_getenv(...).lower() == "true"` (case-insensitive) — line 178
- `MYSQL_SSL` = `_getenv("MYSQL_SSL", "true") == "true"` (**case-sensitive**) — line 112

So `MYSQL_SSL=True`/`TRUE` silently *disables* SSL (anything but exact `"true"` is
falsy). Fix:
- Add `_getbool(key: str, default: bool) -> bool` beside `_getenv` — case-insensitive,
  accepting `true/1/yes/on`.
- `disable_bad_channels = _getbool("DISABLE_BAD_CHANNELS", False)`
- In `_db_config`: `if _getbool("MYSQL_SSL", True):`

(No `_getfloat` needed — the only float, `mirror_failure_ratio_threshold`, is demoted to
a literal in §3, so its inline `float(...)` disappears.)

## 3. Demote never-overridden knobs to in-place literal constants — `dd/common/cfg.py`

These have baked-in sensible defaults and are realistically never overridden per-deploy;
env-backing just inflates the contract. Convert to literals, keeping the `cfg.X` name and
the adjacent explanatory comments (so no call site changes):

| symbol | value | line |
|---|---|---|
| `alert_flush_interval` | `5` | 204 |
| `alert_queue_maxsize` | `1000` | 206 |
| `alert_freq_window` | `300` | 209 |
| `alert_freq_threshold` | `10` | 210 |
| `alert_escalation_debounce` | `600` | 211 |
| `mirror_failure_ratio_threshold` | `0.5` | 215 |
| `mirror_failure_min_sample` | `10` | 216 |
| `announcer_offline_alert_after` | `900` | 219 |
| `embed_warning_color` | `h.Color(0xF1C40F)` | 221 |
| `embed_critical_color` | `h.Color(0x992D22)` | 222 |
| `navigator_timeout` | **`900`** (see note) | 194 |

> **`navigator_timeout` note:** the code default is `120` but `.env-example` documents
> `NAVIGATOR_TIMEOUT=900`, so production almost certainly overrides to 900. Use **900** to
> preserve current behavior — using 120 would silently shorten pagination timeouts. Before
> finalizing, confirm the live `.env` value; if production uses something other than 900,
> use that.

Caveat for whoever actions this: these literal values assume production runs the
documented/default values. If any are actually overridden in the live `.env` to something
else, demoting changes behavior — check the real `.env` first. (All except
`navigator_timeout` match both code default and `.env-example`.)

## 4. Reconcile `.env-example`

**Remove ghosts/dupes** (not read by any code):
- `EMOJI` (line 33) — emojis are fetched live from `kyber_discord_server_id`'s guild
  (`dd/common/utils.py`, `dd/anchor/embeds.py`), never from this var.
- `ADMIN_ROLE` (line 4) — stale rename; code reads `CONTROL_DISCORD_ROLE_ID`.
- duplicate `KYBER_DISCORD_SERVER_ID` (lines 3 **and** 6) — keep one.

**Remove keys demoted in §3:** `ALERT_FLUSH_INTERVAL`, `ALERT_QUEUE_MAXSIZE`,
`ALERT_FREQ_WINDOW`, `ALERT_FREQ_THRESHOLD`, `ALERT_ESCALATION_DEBOUNCE`,
`MIRROR_FAILURE_RATIO_THRESHOLD`, `MIRROR_FAILURE_MIN_SAMPLE`,
`ANNOUNCER_OFFLINE_ALERT_AFTER`, `EMBED_WARNING_COLOR`, `EMBED_CRITICAL_COLOR`,
`NAVIGATOR_TIMEOUT`.

**Add missing real vars** (read by code, absent from example):
- `CONTROL_DISCORD_ROLE_ID` (read at `cfg.py:182`)
- `PORT` (read at `cfg.py:249`, default 8080)
- `MYSQL_PRIVATE_URL` (preferred over `MYSQL_URL`, `cfg.py:225`)

Sanity check afterward: every `_getenv("X")` / `_getbool("X")` key in `cfg.py` should have
a corresponding `.env-example` entry (or a documented default), and vice-versa.

## 5. Remove `cfg.admins` — replace with the owner hook entirely

`cfg.admins` (`cfg.py:183`, fed by the `ADMINS` env var) is a hand-maintained ID list that
duplicates the bot-owner authorization already in `dd/common/auth.py`. Decision: **drop it
and gate on bot ownership instead.**

**The owner primitives** (`dd/common/auth.py`):
- `owner_only` — a CHECKS-step hook (`hooks=[owner_only]`)
- `check_invoker_is_owner(ctx) -> bool` — the predicate, for `owner OR <x>` cases
- backed by `bot.fetch_owner_ids()` (`dd/common/bot.py:107`)

**Access set — broaden to team members (decided):** `fetch_owner_ids()` **already**
returns *all Discord team member IDs* when the app is team-owned, falling back to the
single application owner only when there's no team (`bot.py:112-114`). So preserving
multi-person access needs **no code change** to the hook — it's an *operational* step:
ensure each bot's Discord application is **team-owned** and every intended admin is added
as a **team member**. (If an app is currently single-owner, converting it to a team in the
Discord developer portal is what restores the multi-admin access `cfg.admins` used to
provide.) Add a short note to whoever owns the Discord apps to verify this.

**Call sites to change** (all 4 `cfg.admins` uses):
- `dd/anchor/extensions/posts.py:35,78,109` — `CreatePost`/`EditPost`/`CopyPost`. Anchor's
  client is **already globally owner-gated** (`dd/anchor/__main__.py:50-53`,
  `client_from_app(..., hooks=[owner_only])`), so these
  `if ctx.user.id not in cfg.admins:` blocks are **redundant** — delete them outright
  (and drop their now-unused `cfg` import if nothing else uses it in the file).
- `dd/anchor/utils.py:301` — verify its call path. If it's only reached from anchor
  commands (which are all owner-gated), delete the check; if it's reachable from a
  non-command path, replace with `await check_invoker_is_owner(ctx)` (import from
  `..common.auth`). Confirm before deleting.

**Then remove the config:**
- Delete `admins = [...]` (`cfg.py:183`).
- Remove `ADMINS=...` from `.env-example` (line 5).
- `rg -n 'cfg\.admins|\.admins\b|ADMINS' dd/ .env-example` → nothing left.

---

## Verification (run after implementing)

- `uv run ruff check dd/common/cfg.py dd/anchor/extensions/posts.py dd/anchor/utils.py`
- `uv run ty check` (config touches many modules; catches stragglers from §3/§5)
- `rg -n 'kyber_ls_thumbnail|reset_time_tolerance|cfg\.admins|ADMINS' dd/ .env-example`
  returns nothing.
- Boot both bots against a dev env to confirm import-time parsing + owner gating still
  work: `uv run python -OOm dd.beacon` and `uv run python -OOm dd.anchor` (needs a
  populated `.env`). In anchor, confirm a bot-owner can still create/edit/copy posts and a
  non-owner is rejected ephemerally by `owner_check_error_handler`.
- Confirm both Discord applications are team-owned with all intended admins as team
  members (operational, §5).

## Out of scope (advisory only)

Adopting `pydantic-settings` was considered and **rejected**: it's not a current
dependency, and its Rust `pydantic-core` risks breaking the Android/Termux sync target
(a known hard constraint). The `_getenv`/`_getbool` helper approach above gets the
ergonomics win without the dependency. Revisit only if Android support is dropped or
config grows nested/multi-source.
