# Disable the `/restart` command in production

## Goal

`/beacon restart` and `/anchor restart` restart the bot by exiting the process with a
**non-zero** code (`RESTART_EXIT_CODE = 1`) and letting Railway's `ON_FAILURE` restart
policy bring it back up. Railway treats a non-zero exit as a *crash*, and it applies
**crash-loop backoff** — it will not restart a service that keeps exiting non-zero
indefinitely. So in prod a `/restart` (or a couple in a row) risks tripping that
backoff and leaving the bot **down**.

Gate the restart-via-exit so it only happens in a **test/dev** environment. In **prod**
(`not cfg.test_env`) `/restart` should refuse and explain, taking **no** shutdown action
— leaving the running process untouched is strictly safer than exiting (an `exit 0`
under `ON_FAILURE` would stay down; an `exit 1` is the very crash-loop risk we're
avoiding). Operators redeploy from Railway to actually restart prod.

## Background (as-is)

- The restart is entirely **command-driven** — there is *no* automatic
  "catch-a-fatal-error-and-exit-non-zero" path anywhere. The only non-zero exit in the
  codebase is `/restart`.
- Chain: `/restart` → `Restart.invoke` (`dd/common/controller.py`) →
  `_run_lifecycle(..., exit_code=lifecycle.RESTART_EXIT_CODE)` →
  `lifecycle.request_shutdown(bot, 1)` schedules `bot.close()` → `bot.run()` returns →
  `raise SystemExit(consume_exit_code())` in each `__main__.py` → Railway restarts.
- `make_controller_group` (`dd/common/controller.py`) is **shared by both bots**
  (`dd/beacon/extensions/controller.py`, `dd/anchor/extensions/controller.py`), so a
  single gate there covers both.
- Exit codes live in `dd/common/lifecycle.py` (`STOP_EXIT_CODE = 0`,
  `RESTART_EXIT_CODE = 1`); the module docstring already spells out the Railway
  contract.
- **Env flag.** There is no `is_prod` flag; prod-vs-nonprod is expressed by the
  truthiness of **`cfg.test_env`** (a tuple of test guild IDs; `()` in prod). This is
  already the repo's established prod gate (`web_auth` dev-auth bypass,
  `user_commands` global-vs-per-guild registration, `testing` extension load hook,
  "DEBUG MODE" status).

## Plan

1. **Add a small, documented predicate** `restarts_enabled()` in
   `dd/common/controller.py` returning `bool(cfg.test_env)`, with a docstring capturing
   the Railway crash-loop-backoff reasoning. This is the test seam.

2. **Guard `Restart.invoke`**: when `not restarts_enabled()`, respond (ephemeral) with a
   `cv2_notice` explaining restarts are disabled in prod and that they should redeploy
   from Railway — then `return` without calling `_run_lifecycle`. In a test env,
   behaviour is unchanged.

   - Gate at the **command** level (not inside `_run_lifecycle`) so `stop` is untouched
     and we never enter the mirror-in-progress override flow just to refuse.

3. **Leave `stop` alone.** `/stop` (exit 0) is a deliberate shutdown and is fine in prod.

## Non-goals

- No change to the exit-code machinery or `__main__` exit; no new env var (reuse
  `cfg.test_env`).
- Not touching Railway's restart policy itself (that's infra config, set to
  `ON_FAILURE`); this is purely the in-app guard.

## Verification

- New unit tests in `dd/common/tests/test_controller.py`:
  - `restarts_enabled()` is `False` when `cfg.test_env == ()` and `True` when truthy
    (monkeypatch `cfg.test_env`) — mirroring the existing `test_prod_parity_empty_test_env`
    idiom.
- `make test` / `make lint` / `make typecheck` green.
- Manual (dev): `/beacon restart` still restarts on dev (TEST_ENV set); the guard only
  bites when `cfg.test_env` is empty.
