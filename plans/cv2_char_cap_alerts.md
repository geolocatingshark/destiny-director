# Plan (STUB): Critical char-cap alerts for all CV2 autoposts

> **Status: STUB (2026-07-06).** Captured while taking over the hung anchor CV2
> migration session. Not yet scoped in detail — this records the gap so it isn't
> lost. Related: `plans/autopost_cv2_migration.md`.

## Problem

Components V2 messages hard-cap total text at **4000 chars** (tighter than the old
embed's 4096). When a post overflows, Discord **rejects the whole autopost**. We want
a loud, owner-pinging alert when this happens so a silently-dropped autopost is caught
immediately.

Two gaps found on 2026-07-06:

1. **Xûr's guard fires at ERROR, not CRITICAL — so it does not ping.**
   `dd/anchor/extensions/xur.py:469` truncates on overflow and calls
   `discord_error_logger(...)`, which logs at `logging.ERROR`
   (`dd/common/utils.py:392`). Owners are pinged **only for CRITICAL**
   (`dd/common/discord_logging.py:28,299`). ERROR is promoted to CRITICAL only by a
   "storm" — `alert_freq_threshold=10` hits within `alert_freq_window=300s`
   (`dd/common/cfg.py:222-223`). Xûr posts **weekly**, so a single overflow can
   **never** storm-promote → an ERROR alert lands in the channel but **no owner is
   pinged**. The user wants this to be a **Critical** (pinging) alert.

2. **Only Xûr has a char-cap guard at all.** Lost Sector, Portal Ops, Ada-1, and
   Eververse anchor autoposts have **no** 4000-char check
   (verified: `grep 4000 dd/anchor/extensions/*.py` hits only `xur.py` + a `posts.py`
   help string). If any of those ever overflow, Discord hard-rejects the post with no
   dedicated char-cap alert.

## Goal

Every CV2 autopost that can grow with upstream data emits a **CRITICAL** alert (owner
ping) when its rendered text would exceed the CV2 cap — ideally *before* truncating,
so the alert says which post and by how much.

## Sketch (to refine)

- Add a shared helper (e.g. `dd/common/components.py` or `utils.py`) like
  `guard_cv2_text(text, *, post_name) -> str` that: if `len(text) >= 4000`, logs at
  `logging.CRITICAL` (so it pings) with a clear `operation=f"{post_name} post"`,
  truncates to 4000, and returns it. Reuse across all autoposts.
- Replace the inline Xûr guard (`xur.py:466-474`) with a call to this helper at
  CRITICAL.
- Add the same guard to Lost Sector, Portal Ops, Ada-1, Eververse constructors right
  before building the container.
- Decide truncation vs. drop: truncating keeps *a* post up (current Xûr behaviour);
  confirm that's preferred over failing the post outright.
- Consider a small safety margin below 4000 (emoji substitution and separators add
  chars after the check — verify the count is post-substitution, as Xûr's is).

## Verification

- Unit test: feed an over-4000 body to each constructor; assert it emits a CRITICAL
  record (owner-ping path) and returns <= 4000 chars.
- Confirm `discord_logging` renders a `🚨 CRITICAL` alert and pings for a single
  occurrence (no storm needed).
