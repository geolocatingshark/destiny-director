# Plan: Forgiving auto-disable for persistently-failing mirror destinations

> **Status: DRAFT — design captured during a review discussion on 2026-06-24
> (`feature-lightbulb-v3` branch). NOT approved for execution.** Re-read against the
> current tree before implementing; file/line numbers will have drifted. The headline
> decision (see "Guiding principle" and "Note on complexity") is to prefer the
> *simplest* mechanism that is good enough, not the most precise one.
>
> **Update (2026-07-01): the live Cartesian `WHERE` bug is FIXED.** Verified in
> `MirroredChannel.disable_legacy_failing_mirrors` (`dd/common/schemas.py`): the UPDATE
> now uses the **same predicate as the SELECT** (`enabled AND legacy AND
> legacy_error_rate >= threshold`) instead of rebuilt `src_id IN (...) AND dest_id IN
> (...)` id lists, with an inline comment explaining why. So "Recommendation #1" and the
> first item under "Related, separable cleanups" are DONE — only the broader
> *cadence-independent disable* redesign (the meat of this plan) remains open.

## Context

Mirroring fans one source-channel message out to many destination channels. There are
**two independent failure-handling layers** today:

1. **Per-run, in-memory engine** — `dd/beacon/mirror_core.py`. A kernel returns
   `KernelSuccess | KernelFailure`; failures carry an `ErrorClass`
   (`PERMANENT` / `TRANSIENT`) from `classify_error` in `dd/common/utils.py`.
   `PERMANENT`-classed targets are skipped for the rest of *that run*
   (`_permanently_failed`, `mirror_core.py` ~L315; `targets_to_schedule` ~L379);
   `TRANSIENT` ones retry with randomised backoff (`run_till_completion` ~L466). **This
   layer is well-factored and is NOT what this plan changes.**

2. **Cross-run, DB disable layer** — `dd/common/schemas.py` +
   the tail of `message_create_repeater_impl` (`dd/beacon/extensions/mirror.py`
   ~L746–796). After each SEND it does, per destination:
   - failure → `legacy_error_rate += 1` (`log_legacy_mirror_failure_in_batch`),
   - success → `legacy_error_rate = 0` (`log_legacy_mirror_success_in_batch`),
   - then `disable_legacy_failing_mirrors()` disables any mirror with
     `legacy_error_rate >= 3` (`schemas.py` ~L548), stamping
     `legacy_disable_for_failure_on_date`. Gated by `cfg.disable_bad_channels`
     (`DISABLE_BAD_CHANNELS`, `cfg.py` ~L190; env default `False` but **set `True` in
     prod**, and we want it on — to stop wasting calls on dead channels). So in prod
     auto-disable is **live**, not a dry-run.

This plan concerns **layer 2 only**: when should a destination be *permanently
unfollowed* across runs?

## Goal & the hard constraint

Eventually prune genuinely-dead destinations (channel deleted, bot kicked, perms
revoked) — but with **heavy forgiveness**. The dominant cost is asymmetric:

- Wrongly disabling a healthy destination → **silent loss of service** (bad).
- Never disabling a dead destination → wasted (rate-limited) API calls + log noise (cheap).

So we bias hard toward *not* disabling, and treat any auto-disable as a rare,
well-confirmed event.

## The core problem: a count-of-3 is hostage to post cadence

Current rule disables after **3 consecutive failing runs** (resets on success). The
streak is measured in *runs* (source messages), not time, so it mis-fires for frequent
sources: a chatty source racks up 3 failures **in minutes** during a single short
outage and gets disabled.

The obvious "fix" — a **time-based streak** (store `failing_since`, disable after the
streak exceeds an `N`-hour forgiveness window) — fails in the *opposite* direction, and
this is the insight that drives the design:

> **Any disable signal derived purely from post attempts is hostage to post cadence.**
> Count-based over-counts *frequent* sources; time-based over-counts *infrequent* ones.
> Post cadence across our sources varies by orders of magnitude, so no single
> attempt-derived metric is correct at both ends.

### Canonical counter-example (keep this as the design's test case)

A **weekly** autopost. Two short (~20 min) outages happen to land on the post moment on
consecutive Tuesdays at 17:00 UTC, with no successful post in between to reset the
streak. With a 48h time window:

- Tue wk1 17:00: outage → fail → `failing_since = wk1 Tue 17:00`.
- Tue wk2 17:00: outage → fail → check: `now - failing_since ≈ 7d > 48h` → **disabled.**

A perfectly healthy weekly channel gets unfollowed on two unlucky 20-minute blips,
because wall-clock duration is meaningless when you only *attempt* (and thus only
observe health) once a week. Pure-time is rejected for exactly this reason; pure-count
is rejected because it disables chatty sources on one outage.

## Guiding principle

Stop inferring death from a single attempt-derived metric. Either (A) require *two
metrics that fail in opposite directions to both agree*, or (B) decouple health
observation from posting entirely via background probing.

---

## Option A — require count AND time (both gates must clear)

Disable a destination only when **all** of:

1. `consecutive_failures >= K` (e.g. `K = 3`) — resets to 0 on any success for that
   dest (per-destination, as today);
2. `failing_since` is older than the forgiveness window (e.g. 48h) — `failing_since`
   set when the streak starts, cleared on success;
3. the latest error for that dest was `PERMANENT`-classed (necessary, not sufficient —
   a sustained `TRANSIENT` failure keeps retrying and is never auto-disabled);
4. the run was **not systemic** — i.e. its failures don't look like a broad outage.
   Reuse the ratio logic already computed for alerting in `flag_mirror_failure_ratio`
   (`mirror.py` ~L481; `cfg.mirror_failure_ratio_threshold = 0.5`,
   `mirror_failure_min_sample = 10`). A dead channel fails while its siblings succeed;
   an outage fails everything at once.

The count gate and the time gate cancel each other's blind spots:

| Scenario | `consecutive_failures` | wall-clock streak | disabled? |
|---|---|---|---|
| Chatty source, 2h outage | ≥3 (fast) | 2h < 48h | **No** — time gate saves it |
| **Weekly source, 2 unlucky Tue blips** | **2 < 3** | 7d > 48h | **No** — count gate saves it |
| Weekly source, failing 3 weeks straight | 3 | 21d | Yes (≈1e-7 to be coincidence; it's dead) |
| Channel deleted / bot kicked (any cadence) | climbs | eventually > 48h | Yes |
| Global 20-min outage (many dests at once) | — | — | **No** — systemic suppression (gate 4) |

`K` is effectively "how many distinct posting occasions must fail before we believe
it" — it protects sparse sources. The time gate protects frequent sources during
outages. Both should be `cfg` knobs.

**Cost:** one new column (`legacy_failing_since`, a nullable `DateTime`) alongside the
existing `legacy_error_rate`; an Atlas migration; the post-run write and the disable
query in `schemas.py` extended to set/clear/consult it. Modest. No new background tasks.

**Residual edge cases (accepted):** an isolated (non-systemic) outage on a single
channel lasting > 48h that returns permanent-looking codes the whole time — extremely
rare, and if it truly 404s for 48h the channel is likely actually gone. Three weekly
blips aligning three weeks running — negligible probability.

---

## Option B — decouple health from posting via background probing

The root cause is that we only observe a destination's health when a post fires. If,
once a destination enters a failing state, a **background task probes it on its own
schedule** (a cheap `fetch_channel` / permission check every few hours, independent of
post cadence), then "failing for N hours" means N hours of *actual repeated probe
failures*. A 20-min blip is cleared by the next probe regardless of when the next post
is; the weekly source stops being special.

**Cost:** a periodic probe task (scoped to "suspect" destinations only, which should be
few), extra API calls, and the state machine to enter/leave the suspect set. More code
and moving parts than Option A, but fully cadence-independent — the principled answer.
**Cheaper than first assessed** — see Feasibility: the "permission check" is a cache read,
not an API call.

---

## Feasibility: can we check send-perms without sending? (Yes — already in-repo)

The worry — "don't we need special permissions to check our permissions in a channel
without sending?" — is unfounded. **No special permission and no API write is needed.** A
member's effective channel permissions are a deterministic function of the guild
@everyone perms + the member's role perms + the channel's permission overwrites (everyone
/ role / member) + admin/owner shortcuts — all data the bot already receives over the
gateway just by being in the guild (`GUILDS` intent, which beacon has; the bot's *own*
member needs no privileged member intent).

The repo **already does exactly this**: `toolbox.members.calculate_permissions(member,
channel)` (hikari-toolbox) backs `check_invoker_has_perms` in `dd/beacon/utils.py`. The
same call with the *bot's* member tells us whether we hold `SEND_MESSAGES` (and
`VIEW_CHANNEL`, `SEND_MESSAGES_IN_THREADS`, …) — **no message sent, no extra rate-limit
cost** (it reads cached roles + overwrites).

Implications:
- "Poll the API later" overstates the cost — it's a **cache read**, kept fresh in real
  time by the gateway (`ChannelUpdate`/`GuildRoleUpdate`/member events). A single
  `fetch_channel` REST call right before a disable is a cheap belt-and-suspenders against
  cache staleness/gaps.
- The genuinely-dead cases resolve cleanly even when we *can't* compute a bit: **channel
  deleted** → not cached / `fetch_channel` 404; **bot kicked** → guild gone from cache;
  **`VIEW_CHANNEL` revoked** → channel hidden (no cache entry) / `fetch_channel` 403 —
  each is itself a definitive "can't use this dest" signal. **`SEND_MESSAGES` revoked but
  still viewable** → `calculate_permissions` shows the bit missing.

Caveats (don't treat the perm-check as a universal "can I post" oracle):
- It confirms the **permission-class** failures (the common dead-channel cases), but some
  send failures aren't permission-derived (`classify_error`'s `_PERMANENT_400_CODES`
  50035/50006, forum/announcement posting rules, channel type). Use the perm-check to
  *gate/confirm* a disable alongside the existing `ErrorClass`, not to replace sending.
- Needs the dest's guild + channel (with overwrites) + bot member + roles in cache; for a
  `VIEW_CHANNEL`-less channel there may be no cache entry at all — treat that absence as
  the dead signal.
- Threads compute perms from the parent channel (+ thread membership) — a wrinkle if any
  dests are threads.

Net: the principled "decouple health from posting" answer is cheap. When a SEND fails
`PERMANENT`, immediately compute the bot's perms; if genuinely missing (or the
channel/guild is gone), mark suspect and re-confirm once after the forgiveness window
before disabling — instead of blindly counting to 3.

---

## Note on complexity — investigate simpler solutions first

Building complex, intricate machinery to capture this behaviour *perfectly* is
something we **can** do (Option A's composite gates, or Option B's probing), but we are
**not yet convinced the complexity is justified.** Before committing to either, a future
investigation should weigh genuinely simpler approaches. The likely-simplest:

- **Easy suspicion marking + slow background probing later.** Don't try to make the
  *disable decision* clever at all. When a destination crosses a low bar (e.g. any
  sustained permanent-classed failure), just **mark it "suspect"** and surface it via
  the existing `health_logger` → Discord alert. Do **not** auto-disable. Optionally, add
  a slow, low-priority background prober *later* that confirms suspects over a long
  window before either auto-disabling or escalating for a human to pull the trigger
  (`undo_auto_disable` / a manual disable command already exist).

This keeps `disable_bad_channels` effectively a human-in-the-loop suggestion, which fits
the cost asymmetry (wrongly disabling is far worse than never disabling). The richer
Options A/B should only be built if data shows wasted retries on dead channels are an
actual, measurable problem.

## Recommendation

(Auto-disable is **on in prod and wanted**, so "turn it off" is not an option; the goal
is accuracy. Perm-checking is cheap — see Feasibility.)

1. ✅ **DONE (2026-07-01) — was urgent, now fixed:** the live Cartesian `WHERE` bug is
   resolved so the *current* prod auto-disable can no longer disable error_rate-0 rows.
   (Shipped on its own as a correctness fix; see the top-of-file Update note.)
2. **Make the decision accurate, don't widen the count.** On a `PERMANENT` send failure,
   re-check the bot's perms in the dest (free, local) and count toward disable only when
   perms are genuinely missing or the channel/guild is gone. Decouples the decision from
   post cadence — the whole point — with almost no new machinery, subsuming most of what
   Options A/B were for.
3. Layer on **Option A**'s time gate + non-systemic suppression only if perm-confirmed
   disables still mis-fire. **Option B**'s scheduled re-probe is now cheap (cache read)
   and is the natural home for "confirm a suspect after the forgiveness window."

Whatever is built, the canonical weekly-source / two-Tuesday-blips scenario above must
be a regression test.

---

## Related, separable cleanups (same area, surfaced in the same review)

Independent of the forgiveness design; can land separately and earlier:

- **Cartesian `WHERE` bug** in `disable_legacy_failing_mirrors` (`schemas.py`) —
  **✅ FIXED (2026-07-01); was LIVE in prod.** It used to disable on `src_id IN (...) AND
  dest_id IN (...)` rebuilt from the matched pairs, which over-matched when the sweep
  returned multiple distinct sources (could disable an innocent `(srcA, destB)` whose
  error_rate is 0) — live under `DISABLE_BAD_CHANNELS=true`. The fix shipped: the UPDATE
  now uses the *same predicate* the SELECT used (`enabled AND legacy AND
  legacy_error_rate >= threshold`), not rebuilt id lists (verified, with an explanatory
  inline comment in `dd/common/schemas.py`).
- **Duplicated retry-with-backoff preamble** in `handle_waiting_for_crosspost`
  (~L533), `message_update_repeater_impl` (~L815), `message_delete_repeater_impl`
  (~L933). Extract one helper. Two latent bugs to fix while doing so: (a)
  `backoff_timer += 30 / backoff_timer` barely grows (increment shrinks as the timer
  grows); (b) these three loop **forever** with no cap/give-up (unlike
  `refresh_server_sizes` / `start_progress_logger`). Per the forgiveness lens, prefer a
  *capped long backoff that keeps trying* over a silent give-up for the send path; if it
  does give up, it must alert, not `return` quietly.
- **Dead code:** `log_legacy_mirror_failure` (singular, `schemas.py` ~L477) has no
  callers — only the `_in_batch` variant is used. Delete it.
- **Misleading alert denominator:** the disable alert (`mirror.py` ~L778) compares a
  *global* `num_disabled` against this-run-only `control.total_targets`, so the
  fraction-based critical/error escalation can mis-fire. Scope the count to this run, or
  fix the denominator.
