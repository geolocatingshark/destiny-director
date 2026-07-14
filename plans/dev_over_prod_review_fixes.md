# Plan: act on the dev-over-prod code-review findings

> **Status: PARTIALLY DONE — captured 2026-07-02; #4, #5, #6, #8, #10 landed
> 2026-07-14 (commit `32b452c`, local `dev`). #1, #2, #3, #7, #9 remain.**
> This is a triage doc. The user picks which of the *remaining* findings to fix (see
> **Decisions needed** below), then a later agent implements the chosen ones. Re-verify
> every symbol + line number against the current tree (grep by name — this repo shifts
> under you) before editing.

## Completed (landed 2026-07-14, commit `32b452c`)

- **#6** — eververse `history_len` 4 → 14 (restored ~14d back-history for the daily rotator).
- **#5** — `/beacon info` now guards only the mirror-count gather; a DB blip appends
  `(mirror counts unavailable)` instead of sinking the diagnostic.
- **#4** — added the `follow_link_single_step` 5xx retry-then-fallback test.
- **#8** — pluralise "role"/"roles" in `explain_missing_permission`.
- **#10** — dropped the stale `/surges` field from the `_rendered_parity` docstring.

Removed from the finding lists below; only the open items (#1, #2, #3, #7, #9) remain.

## Context

Produced by a multi-agent code review (Workflow: 22 agents — 6 subsystem reviewers, then
one adversarial refute-by-default verifier per finding) over the **`shark/main..dev`
diff**: the commits sitting on `dev` that prod (`shark/main`) does not yet have. At review
time that was 16 commits; prod was fully in sync (`shark/main == origin/main == main`).

Result: **16 raw findings → 10 confirmed, 6 refuted.** The signal concentrated in the
newest/most-complex change, the autopost permission gate (5 of 10 findings), which came
from `plans/autopost_permission_diagnostics.md`.

None of these block correctness of the *common* path; the headline item (#1) is a
false-negative that refuses a working setup. Fixes are independent — cherry-pick freely.

## My recommendation (advice requested)

The cheap/self-contained items (#4, #5, #6, #8, #10) are done. What's left is the
autopost permission-gate cluster:

- **Fix before the next prod deploy:** **#1** (thread autoposts falsely blocked — a real
  user-facing regression in brand-new gate code).
- **Same pass, if you want the gate to feel finished:** the autopost UX cluster **#2**,
  **#3**, **#7** — #3+#7 share a root cause (redundant channel fetches), and #2 is the
  product call below.
- **Skip / opportunistic:** **#9** is cosmetic (fine as-is at this scale).

## Confirmed findings

### 🟠 Medium

**#1 — Thread autoposts over-require `SEND_MESSAGES` → gate false-blocks a working setup**
`dd/beacon/extensions/autoposts.py` (`for_channel`, ~L101-114; `_AUTOPOST_PERMS`
`SEND_MESSAGES` entry ~L80-85)
`for_channel()` keeps `SEND_MESSAGES` required for every target and merely *appends*
`SEND_MESSAGES_IN_THREADS` for threads, so a thread requires **both**. But thread
autoposts always go legacy (`_WEBHOOK_FOLLOW_TARGET_TYPES = {GUILD_TEXT}`) → delivery is
`channel.send()` into the thread (`dd/beacon/extensions/mirror.py` `_send_one`, ~L752),
which Discord gates on `SEND_MESSAGES_IN_THREADS` alone. Perms resolve against the *parent*
(`dd/beacon/utils.py` `_resolve_bot_member_channel`, ~L165). So a guild that denies Send
Messages on the parent (e.g. `@everyone` override on a locked channel) but grants
Send-in-Threads can post fine, yet `/autopost … enable` reports `SEND_MESSAGES ❌ required`
and refuses. **Fix:** for a thread target, drop `SEND_MESSAGES` from the required set (or
mark it advisory) and rely on `SEND_MESSAGES_IN_THREADS`. Update
`dd/beacon/tests/test_autopost_perms.py` (the thread test currently asserts both bits).

### 🟡 Low

**#2 — `MANAGE_WEBHOOKS` labelled "advisory" but its absence still hard-fails text enables**
`dd/beacon/extensions/autoposts.py` (advisory entry ~L70-72, `_enable_autopost` follow path
~L448-453)
Plain `GUILD_TEXT` + no ping_role takes the follow path (`rest.follow_channel`), which needs
`MANAGE_WEBHOOKS` and returns 50013/403. `_enable_autopost` only catches `BadRequestError`
(`ForbiddenError` is a *sibling*, not a subclass), so it propagates and renders a
self-contradictory embed: every *required* perm ✅, and the only ❌ tagged "(recommended)".
No legacy fallback fires; the "degrades gracefully" comment is false. **Fix (pick one):**
(a) catch the 403 from `enable_non_legacy_mirror` and fall back to `enable_legacy_mirror`;
or (b) keep `MANAGE_WEBHOOKS` required so the gate blocks with an accurate message.

**#3 — Silent bare `return` after defer when the two channel fetches disagree**
`dd/beacon/extensions/autoposts.py` ~L556-559
Perms and target-channel come from two independent REST fetches. If the *second* transiently
fails (`HikariError → (None, False)`) while the gate already passed on non-None perms, the
`if target_channel is None: return` fires *after* `ctx.defer()` with nothing responding →
the interaction hangs (Discord "thinking…" forever). **Fix:** respond with
`permission_error_embed`/`autopost_error_embed` instead of bare-returning; ideally resolve
perms + target channel from a single fetch (see #7) so the state can't arise.

### ⚪ Nits (cleanup, non-blocking)

- **#7 — Same channel fetched up to 4× per enable inside the open DB transaction** ·
  `dd/beacon/extensions/autoposts.py` (~L504, 513, 530, 534). Root cause of #3. Fetch the
  target (and parent) once and thread it through `resolve_bot_perms` /
  `_fetch_target_and_view_state`. (Note: SQLAlchemy acquires the pooled connection lazily on
  first SQL, so the "connection held across REST" framing is overstated — it's redundant
  round-trips on an infrequent admin command.)
- **#9 — N parallel COUNT queries where one `GROUP BY` would do** · `dd/common/controller.py`
  ~L227-232. Optional `MirroredChannel.count_dests_bulk(src_ids)` returning `dict[int,int]`.
  Fine as-is at this scale.

## Refuted findings (recorded for transparency — do NOT action)

Six were thrown out by the adversarial verifiers; the notable rigorous catches:

- **gzip decompression-bomb in `builders_link.py`** — refuted: owner-only command, input
  hard-capped at 6000 chars (≈4MB / ~220ms), no lower-privilege actor to defend against.
- **Type-1 handler no-defer blows the 3s deadline** — refuted: pre-existing (identical in
  v2), and the url-latency diff *reduces* the exposure (10s/2-retry vs 30s/10-retry, now
  parallel). Root cause (missing defer) is untouched by the diff.
- **No 2000-char guard on `/…info`** — refuted: not reachable (~11 lines / <1000 chars;
  would need ~40 code-registered followables), owner-only anyway.
- Per-call `aiohttp.ClientSession` in `builders_link.py` (established repo idiom);
  defer-before-network on `/post components` (pre-existing, owner-gated);
  sector-items `additionalProperties:false` (pre-existing; the "fix" would reject legacy
  stored JSON mid-cutover).

## Decisions needed from you

1. **Scope:** which of the remaining findings to fix? (Recommended: **#1** now; the
   autopost UX cluster **#2, #3, #7** soon; **#9** skippable.)
2. **#2 direction:** legacy fallback on the 403 (feature works without Manage Webhooks) vs.
   make `MANAGE_WEBHOOKS` a hard requirement (accurate block). This is a product call.
3. **Delivery:** one branch for the lot, or split (e.g. #1, then the autopost UX cluster)?

## Repo rules
uv only; ruff line-length 88 + double quotes; ty types; async throughout;
`@loader.task` → `max_failures=-1`. Verify with `make test` (SQLite by default — do **not**
set `TEST_USE_MYSQL`, which points tests at the dev DB). Never deploy to prod without
explicit sign-off. Related: `plans/autopost_permission_diagnostics.md` (source of #1–#3, #7).
