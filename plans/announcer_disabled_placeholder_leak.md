# Plan: stop disabled autoposts from leaking the "Waiting for data…" placeholder

> **Status: APPROVED, not yet implemented (2026-07-07).** Deferred until other
> in-flight changes land, but **HIGH PRIORITY overall** — this is a live,
> user-visible bug on every disabled/never-enabled autopost. Fix as soon as the
> current work clears. Re-verify symbols + line numbers against the tree before
> executing (grep by name — this repo shifts under you).

## Problem

The shared announcer `api_to_discord_announcer` (`dd/anchor/extensions/xur.py`)
posts its "Waiting for data from the API…" placeholder **before** it checks
whether the autopost is enabled:

1. `utils.send_message(...)` posts the placeholder unconditionally (~xur.py:562).
2. Only *inside* the following retry loop (~xur.py:576-581) does it run
   `if check_enabled and (enabled_check_coro is None or not await enabled_check_coro()): return`.

So when an autopost is disabled via the `auto_post_settings` DB table (or was
never enabled — `AutoPostSettings.get_enabled` returns `None`, which the gate
treats as disabled), the cron still fires, the placeholder is posted, and the
function returns immediately. The placeholder is never edited to real content,
never crossposted, and never cleaned up — leaving an orphan "Waiting for data
from the API…" message in the channel.

All four scheduled autoposts route through this one function, so they all leak:
`portal_ops`, `lost_sector`, `eververse`, `xur`.

## Origin (for context)

Not a regression — original behaviour. The placeholder-before-check ordering has
existed since `128f277` "Add custom xur announce logic" (2024-04-20). It became
widespread as more autoposts adopted the shared announcer, and more visible after
the CV2 migration (`682f091`, `9ebfd39`) turned the placeholder into a standalone
Components V2 container.

## Fix

Add an **early enabled-check at the top of `api_to_discord_announcer`, before the
placeholder `send_message`**:

```python
if check_enabled and (
    enabled_check_coro is None or not await enabled_check_coro()
):
    return
```

Bail out before posting anything. Keep the two existing in-loop checks as guards
against mid-run toggling. One change fixes all four autoposts at once.

## Verification

- Unit/behavioural: with `check_enabled=True` and an `enabled_check_coro` that
  returns `False`/`None`, assert `utils.send_message` is **not** called and the
  function returns without posting.
- Confirm the enabled path (coro returns `True`) still posts + edits as before.
- `make check` (ruff + ty + `-m "not discord"` suite).
- Dev runtime: disable `portal_ops` in the dev DB, trigger the cron (or the
  per-minute test crontab), confirm no placeholder appears.

## Notes / edge cases

- `enabled_check_coro is None` currently also short-circuits to "disabled" when
  `check_enabled=True`; preserve that semantics in the early check (matches the
  in-loop checks).
- The manual `send` command uses `check_enabled=False`, so it is unaffected and
  should still post regardless of DB state.
