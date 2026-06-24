# Memory-leak backlog (non-mirror)

> **For a fresh Claude Code session:** self-contained, but **re-verify by symbol name
> (grep), not line number** — refs are a point-in-time snapshot and will drift. Follow
> `CLAUDE.md` (uv, ruff line-length 88 + double quotes, ty, async throughout, tests under
> `dd/<pkg>/tests/`). Never deploy to prod.

## Context

A project-wide memory-leak audit found a small set of unbounded-growth / leaked-resource
spots outside the mirror subsystem. Both bots are long-lived single processes, so slow
unbounded growth matters over weeks of uptime. **Mirror-subsystem leaks are tracked
separately** in `plans/mirror_improvements.md` (see its "Memory-leak addendum"). This file
is the non-mirror backlog. None of these are urgent; N1 is the most worthwhile.

## Leaks

### N1 — `DiscordLogHandler._last_escalation` never evicted  **(MED — do first)**
`dd/common/discord_logging.py` (`_last_escalation` dict; written in `_ping_allowed`).
One entry per unique escalated **signature** (`logger|levelno|identity`) is added and
**never removed**. Its sibling `_sig_times` *is* cleaned (the `del self._sig_times[sig]`
when its window empties) — the asymmetry is the bug. Grows with the diversity of error
signatures that reach CRITICAL/ping over uptime.
- **Fix direction:** evict `_last_escalation[sig]` once `now - last >
  alert_escalation_debounce` (the entry is useless past the debounce window) — symmetric to
  the `_sig_times` cleanup. Easiest spot: prune opportunistically in `_flush`/`_is_storm`,
  or drop the entry in `_ping_allowed` when it's already stale.
- **Test:** feed many distinct signatures, advance `time.monotonic` past the debounce
  window, assert `_last_escalation` shrinks back down. Pure unit test, no DB.

### N2 — `OAuthStateManager._oauth_state_codes` abandoned codes  **(LOW)**
`dd/anchor/extensions/bungie_api/oauth.py` (`_oauth_state_codes` dict).
A generated login state code is only removed on `consume_oauth_state_code` or when
`check_state_code_exists` happens to see it expired. An abandoned `/bungie login` (owner
generates a code, never completes) leaves the code until something checks it. Owner-only,
very low rate → tiny, but unbounded in principle.
- **Fix direction:** sweep expired codes in `generate_oauth_state_code` (cheap, runs on each
  login) or on a light timer.
- **Test:** insert an expired entry, call `generate_oauth_state_code`, assert the expired
  entry is gone.

### N3 — `download_linked_image` leaks an aiofiles handle on error  **(LOW-MED)**
`dd/anchor/utils.py` (`download_linked_image`). The file is opened with
`f = await aiofiles.open(name, "wb")` then `await f.write(...)` / `await f.close()` — **not**
a context manager. If `resp.read()` or `f.write(...)` raises, `close()` is skipped → fd leak.
- **Fix direction:** `async with aiofiles.open(name, "wb") as f: await f.write(await resp.read())`.
- **Test:** monkeypatch the write to raise, assert no handle is left open (or just rely on
  the structural change + ruff).

### N4 — nav auto-update listener + task never deregistered  **(LOW — latent)**
`dd/beacon/nav.py` (`_setup_autoupdate`). Each `NavPages` registers a `@self.bot.listen()`
`history_updater` and a `lookahead_update_task`, neither of which is ever removed. In normal
operation this is **bounded** (~one per followable, created once at startup), so it is *not*
an active runtime leak. It only accumulates if `NavPages` instances are recreated (e.g.
`StartedEvent` re-fires on reload). Low priority.
- **Fix direction:** give `NavPages` a teardown that unsubscribes the listener and cancels
  `self._lookahead_task`; call it before recreating, or guard against double-setup. Only
  worth doing if hot-reload/recreation becomes real.

## Not leaks (recorded to avoid re-investigation)
- **Manifest rebuild** (`bungie_api/manifest.py::_build_manifest_dict`): builds a large
  multi-table dict per autopost with no caching → transient high-water memory, but the old
  copy **is** GC-reclaimed. A caching opportunity (see `plans/bungie_api_http_client_split.md`),
  not a leak.
- **`bot.py` emoji refresh** `_ = asyncio.create_task(self._refresh_emoji_loop())`: discards
  the task ref — the *opposite* risk (weak-ref GC could cancel it mid-flight), a robustness
  nit, not a leak. `nav.py` keeps a strong ref correctly.
- **`ServerEmojiEnabledBot.emoji`**: overwritten each 4-min refresh → bounded.

## Verification (general)
Runtime spot-check for any of the above: temporarily log `len(...)` of the container on a
timer, or take `tracemalloc` snapshots across the relevant cycle, and confirm flat vs
growing. Unit tests above are the durable guard.
