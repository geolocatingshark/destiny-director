# Plan — usage-gate: handoff-into-fresh-session wake (token-efficient resume)

**Status:** deferred. The scheduled auto-wake is currently **disabled**; the gate
pauses the session only (see `docs/usage-gate.md`, "Temporary" note). This plan is the
intended replacement. Delete this file once implemented.

## Context

The usage-gate hook (`~/.claude/hooks/usage-gate.sh`, user-level, not in git —
documented/reproduced in `docs/usage-gate.md`) blocks tool calls when the 5-hour
(≥90%) or 7-day (≥95%) Claude subscription cap is hit.

It used to tell Claude to call `ScheduleWakeup(delaySeconds → reset)` and auto-resume.
That was removed because **every wake re-reads the entire conversation transcript
uncached** — these waits always span more than the 5-minute prompt-cache TTL, so each
wake is a full-price input re-read, and it can happen 2–3× per episode (see "Extra
reloads" below).

## The idea

Don't resume the fat session. At block time the whole history is **already loaded in
the active context**, so serialising it to a small handoff is nearly free. Then resume
into a **fresh, minimal session** that reads only that handoff.

- Resume-same-session: re-read whole transcript on each wake (expensive, uncached).
- Handoff + fresh session: cheap summary now + read a small file later.

## Mechanism gotcha (the crux)

`ScheduleWakeup` **resumes this same session** — it does not start a fresh one. So the
handoff only pays off if the wake lands in a NEW session. Options:

1. **Local cron (preferred on this box).** Register a one-shot `at`/cron line that runs
   `claude -p "Read <handoff> and continue."` at `reset + buffer`. Brand-new session,
   zero transcript, entirely outside the model.
2. `schedule`/CronCreate routine to the same effect.
3. Manual: user reopens a session after reset pointed at the handoff.

## To build

1. **Handoff writer.** On block (well-scoped remaining work only), write
   `~/.cache/claude-handoff-<session_id>.md`: remaining task, key decisions, touched
   file paths, exact next action, and any invariants. Plus a **one-line memory
   breadcrumb** pointing at it (do NOT dump the body into `MEMORY.md` — it loads into
   every future session).
2. **Fresh-session scheduler.** Small helper (e.g. `usage-gate-handoff.sh`) that
   registers the cron/`at` job for `resume_at + buffer` invoking
   `claude -p "Read <handoff>; continue; then delete the handoff and cron line."`
3. **Hook message.** Change the block text to: *if remaining work is well-scoped, write
   the handoff + memory pointer and schedule a fresh session; otherwise pause only.*
4. **Cleanup.** Fresh session deletes its handoff file and the cron entry when done.

## Fold in the earlier efficiency fixes (make wakes exactly 1)

- **Max reset across blocking windows.** When both caps are over, schedule for the
  LATEST `resets_at` so one wake clears both (avoids waking at the 5-hour reset only to
  re-block on the still-high 7-day). The pause-only version already reports
  `max(resets_at)` as the resume time — carry that into the scheduler.
- **Cache-bust at the boundary + buffer.** The usage cache is up to `TTL=90s` stale and
  the server reset can lag; schedule `reset + ~120s` and treat the cache as expired once
  `now >= cached resets_at`, so the post-wake check refetches fresh and doesn't bounce.

### Extra reloads the current (pre-disable) design could cause

1. Both caps over → wakes at 5-hour reset, 7-day still ≥95% → re-block → reschedule to
   7-day reset. One wasted full reload.
2. Cache staleness → wake lands at `resets_at`, hook reads pre-reset cached value →
   false re-block → reschedule. Another wasted reload.

## Tradeoff

A fresh session loses in-context nuance not captured in the handoff. Use handoff+fresh
for **well-scoped tail-of-task** work; for murky mid-investigation state, a single
resume-reload may be worth the tokens. The hook message should make the agent choose.
