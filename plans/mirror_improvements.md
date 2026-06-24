# Mirror / Announce Subsystem Improvements

> **For a fresh Claude Code session:** This document is self-contained — you can start
> from it alone. Before implementing, **re-read the code referenced below and re-verify it
> still matches this plan.** All `file:line` references are a point-in-time snapshot of
> `dd/beacon/extensions/mirror.py` and friends and **will drift** — confirm symbols by name
> (grep), not by line number. **If the code has changed materially since this was written,
> revise this plan accordingly before/while implementing**, and call out what changed.
> Follow the repo rules in `CLAUDE.md` (uv, ruff line-length 88 + double quotes, ty, tests
> under `dd/<pkg>/tests/`, async throughout). Never deploy to prod.

## Context

The Discord **mirror** subsystem (`dd/beacon/extensions/mirror.py`) broadcasts a
source-channel message to many target channels — this is how announcements / autoposts fan
out. It is built around three classes plus a per-operation "kernel" closure that does the
actual API work:

- `KernelWorkTracker` — accounting (which targets are scheduled / done / failed / retrying).
- `KernelWorkControl(KernelWorkTracker)` — orchestration (`run_till_completion`, `cancel`).
- `KernelWorkControlRegistry` — per-source-message serialization via an `aio.Lock`; the
  module-global singleton is `kernel_work_control_registry`. Its `cancel()` currently only
  permits `MirrorOperationType.UPDATE`.
- "kernel" = an async closure `kernel(control, ch_id, msg_id, delay)` built inside each of
  `message_create_repeater_impl` (SEND), `message_update_repeater_impl` (UPDATE),
  `message_delete_repeater_impl` (DELETE). It calls `control.report_scheduled(...)`,
  `await aio.sleep(delay)`, does the Discord API work inside `async with discord_api_semaphore:`,
  then `control.report_completed(...)` or, on `except Exception`, `logging.exception(e)` +
  `control.report_failure(...)`.

Rough edges this change addresses:

- **Kernel coupling**: kernels mutate the control via `report_scheduled/_completed/_failure`
  side effects and embed their own retry `aio.sleep(delay)`, mixing work, scheduling and state.
  Hard to test and reason about.
- **Unbounded fan-out**: `run_till_completion` creates one asyncio task *per target at once*
  then `aio.wait(self._tasks, ALL_COMPLETED)`; `self._tasks` is a set that only grows (done
  tasks never released), and a single slow retry-sleep (held *inside* a task) blocks the whole
  round.
- **Rate limiting is muddled**: `TimedSemaphore(value=45)` (an `aio.Semaphore` whose
  `__aexit__` sleeps `period=1`s before releasing) conflates concurrency and rate. A big
  announcement can consume nearly all of Discord's ~50/s global budget and starve interactive
  commands and other bot functions.
- **No error classification**: every failure is `logging.exception`-ed per channel and retried
  up to the threshold — including permanent failures (deleted channel, missing perms), wasting
  rate budget and flooding logs.
- **Progress logging is fragile**: `log_mirror_progress_to_discord` +
  `_continue_logging_mirror_progress_till_completion` build an `h.Embed` and mutate it by
  **magic numeric field indices** (`COMPLETED=2, RETRYING=3, FAILED=4, REMAINING=5,
  TIME_TAKEN=6, TIME_TAKE_TO_TRY_ALL_ONCE=7`), duplicate the time-formatting logic, back off as
  `5**tries` (up to 125 s blackouts), and use **miru** (`LogCancelButton`, `m.View`) for the
  cancel button while the rest of the new UI is Components V2 + lightbulb components.

**Outcome**: a cleaner, testable kernel architecture; strictly bounded, headroom-preserving
parallelism; signature-based error classification (reusing the logging system's approach); a
clearer Components V2 progress message; and a **reconciliation / convergence model** so the
bot always brings every destination into sync with the source's current state.

## Relevant existing code (verify before relying on)

- `dd/beacon/extensions/mirror.py`
  - `TimedSemaphore` (~line 44), `discord_api_semaphore = TimedSemaphore(value=45)` (~68).
  - `MirrorOperationType` enum SEND/UPDATE/DELETE (~71).
  - `KernelWorkControlRegistry` (~79); `register`, `lock_source_message`, `cancel`
    (UPDATE-only restriction ~126); singleton `kernel_work_control_registry` (~136).
  - `KernelWorkTracker` (~139): `source.keys().__iter__().__next__()` hack (~154);
    `report_scheduled/_completed/_failure`; properties `failed_targets` (~187/depends on
    `_tries >= retry_threshold`), `successful_targets`, `targets_not_yet_tried`,
    `targets_being_retried`, `is_every_target_tried`, `targets_to_schedule` (~225),
    `is_work_left_to_do`.
  - `KernelWorkControl` (~250): `run_till_completion` (~274-293), `cancel` (~295).
  - `_continue_logging_mirror_progress_till_completion` (~338), `LogCancelButton` (~415, miru),
    `log_mirror_progress_to_discord` (~426).
  - `flag_mirror_failure_ratio` (~573) + `health_logger = logging.getLogger("dd.beacon.mirror.health")`.
  - Kernels / impls: `message_create_repeater_impl` (~695; mirrors computed via
    `MirroredChannel.fetch_dests`, role pings via `fetch_mirror_and_role_mention_id`, post-run
    DB batch write via `aio.gather(... log_legacy_mirror_failure/success_in_batch,
    MirroredMessage.add_msgs_in_batch ...)` ~818); `message_update_repeater_impl` (~878; targets
    from `MirroredMessage.get_dest_msgs_and_channels` ~882; `retry_threshold=2`;
    `enable_cancellation=True`); `message_delete_repeater_impl` (~976; `retry_threshold=2`).
  - Admin command `MirrorCancel` (~1303) routes to `kernel_work_control_registry.cancel(...)`.
- `dd/common/discord_logging.py` — error-signature system: `_normalize` (~76),
  `identity_for_exc` (~80), `_record_identity` (~89), `reference_code` (~96; blake2s→base32,
  6-char). `DiscordLogHandler` batches/dedups by `signature` and renders Components V2 via
  `build_container`; storm-promotes repeated errors to CRITICAL and pings owners (debounced).
- `dd/common/components.py` — CV2 helpers: `text_display`, `separator`,
  `build_container(text_sections, *, accent_color)` (~124), and `Paginator` built on
  `lightbulb.components.Menu` (the pattern to copy for the cancel button: register buttons on a
  `Menu` for the callback router, render visually-identical buttons with the same `custom_id`
  inside the CV2 container — see ~218-235, 272-278). `build_container` does **not** support
  thumbnails/images (would need a `Section` + `Thumbnail` accessory).
- `dd/common/cfg.py` — `_getenv` settings pattern; existing
  `mirror_failure_ratio_threshold`/`mirror_failure_min_sample` parsed as float (~244).
- `dd/beacon/extensions/mirror_tracing.py` — imports `TimedSemaphore` from `mirror` and uses
  `TimedSemaphore(value=1)`. **Keep that working** (keep the class exported / re-exported).
- `dd/beacon/__main__.py` — registers `lb.Client` as a DI value (injectable via
  `client: lb.Client = lb.di.INJECTED`); installs miru (`miru.install`).
- Versions: `hikari==2.5.0`, `hikari-lightbulb>=3.0.0`, `hikari-miru>=3.4.0`.

### Verified library facts (load-bearing)
- An **interactive button on a bot-posted (non-interaction) CV2 message is feasible**:
  `lightbulb.components.Menu` routes purely by registering a handler on the client and matching
  `interaction.custom_id`, independent of how the message was created (this is exactly what
  `Paginator` does). Use **`Menu.attach_persistent(client, timeout=...)`** (background,
  non-blocking), **not** `Menu.attach` (which blocks until stop/timeout). `Paginator.send` uses
  blocking `attach` only because it runs inside a command invocation.
- `asyncio.TaskGroup` cancels all siblings if one child raises — prefer a `Semaphore`-gated
  worker pool with `gather(..., return_exceptions=True)` (kernels return outcomes and shouldn't
  raise, but the pool is robust to a stray bug and bounds live coroutines regardless of target
  count).

## Decisions (confirmed with user)
- Global mirror rate cap: **30/s** (leaves ~20/s headroom under Discord's ~50/s).
- Extract pure logic to **`dd/beacon/mirror_core.py`** (unit-testable, no Discord I/O).
- Cancel button: **migrate off miru to lightbulb components** (Menu router + CV2 action row).
- Progress message: **drop the source-image thumbnail** (simpler CV2 container).
- **Reconciliation / convergence model**: mirror ops bring every destination channel in sync
  with the source's current state. Cancel a partial send → the dests that got the message are
  persisted; a later source edit **reconciles**: edits dests that have the message AND sends to
  dests still missing it.
  - Worked example: source msg 832 announced, reaches 50% of servers, send is **cancelled**
    (DB updated to reflect which dests got it). Source 832 is later **edited** → the 50% that
    have it are **edited**, the 50% that never received it get a **fresh send**. All
    destinations end matching 832.
- **Cancel button on SEND *and* UPDATE** (DELETE runs to completion). Relax the
  `KernelWorkControlRegistry.cancel` UPDATE-only restriction.
- **Full reconcile on edit**: `message_update_repeater_impl` edits existing dest messages and
  sends to missing dests so all destinations converge.
- **Persist successes-so-far at cancel** so a later reconcile knows which dests have the message.
- (Implementer's calls) Retry sleep happens **outside** the concurrency slot; all
  `BadRequestError` (400) treated as **permanent** except the already-special-cased "already
  crossposted" crosspost case. `cancel()` **gracefully drains** in-flight workers (stops
  scheduling new targets, lets running API calls finish and record) rather than
  hard-`task.cancel()` mid-call — avoids a dest being sent Discord-side but unrecorded in the DB
  (which would cause a double-send on later reconcile).

---

## Implementation

### 1. Shared helpers in `dd/common/utils.py`
- **Move** `identity_for_exc` and `reference_code` from `dd/common/discord_logging.py` into
  `utils.py` (pure functions, no Discord dependency). Re-import them in `discord_logging.py` so
  its public surface is unchanged (`_normalize`, `_record_identity` stay in the handler).
  `mirror.py` already imports from `utils`, avoiding heavier coupling to the logging handler.
- **Add `format_duration(seconds: float) -> str`** — replaces the duplicated inline formatting
  in the two progress functions.
- **Add `ErrorClass` enum + `classify_error(exc) -> ErrorClass`**:
  - `PERMANENT` (no retry): `h.ForbiddenError` (403; codes 50001 Missing Access, 50013 Missing
    Permissions), `h.NotFoundError` (404; codes 10003 Unknown Channel, 10008 Unknown Message,
    10004 Unknown Guild), `h.BadRequestError` (400; e.g. 50035, 50006), `h.UnauthorizedError` (401).
  - `TRANSIENT` (retry w/ backoff): `h.RateLimitedError`/`h.RateLimitTooLongError` (429),
    `h.InternalServerError` / any `h.HTTPResponseError` with `status >= 500`,
    `asyncio.TimeoutError`, connection errors.
  - Fallback: unknown exception → `TRANSIENT` but logged once (so it surfaces).
  - Implement via `isinstance` first, then refine `h.HTTPResponseError` by `.status`/`.code`.
    **Verify `.code`/`.status` attribute names against the installed hikari 2.5 first.**

### 2. New `dd/beacon/mirror_core.py` (pure logic, no Discord I/O)
Move here (out of `mirror.py`): `MirrorOperationType`, `KernelWorkTracker`, `KernelWorkControl`,
`KernelWorkControlRegistry` (+ the singleton). Add:

```python
@dataclass(frozen=True, slots=True)
class KernelSuccess:      channel_id: int; message_id: int
@dataclass(frozen=True, slots=True)
class KernelFailure:      channel_id: int; exc: BaseException
                          error_class: ErrorClass; reference_code: str
KernelOutcome = KernelSuccess | KernelFailure

class MirrorKernel(t.Protocol):
    async def __call__(self, ch_id: int, msg_id: int | None) -> KernelOutcome: ...

class RateLimiter:   # token bucket; refills `rate`/s, no sleep-on-release
    def __init__(self, rate: float): ...
    async def acquire(self) -> None: ...
    async def __aenter__(self): ...
    async def __aexit__(self, *exc): ...
```

Keep `TimedSemaphore` exported from `mirror.py` (or re-export) for `mirror_tracing.py`.

### 3. Tracker changes (`KernelWorkTracker`)
- Clean up the `source` "mapping-of-1" hack — take `source_channel_id`/`source_message_id`
  directly (replace `source.keys().__iter__().__next__()`).
- Add `_permanently_failed: set[int]` and `_failures: dict[int, KernelFailure]`.
- Add `_apply_outcome(outcome)` — the single place state mutates: `report_completed` on success;
  on failure record into `_failures`, `report_failure`, and if `PERMANENT` add to
  `_permanently_failed`.
- Add a `_cancelled: bool` flag; `targets_to_schedule` returns empty when set (graceful drain —
  no new scheduling/retries) and also excludes `_permanently_failed`; `failed_targets` excludes
  `_permanently_failed`.
- Add a `failure_breakdown` property → `Counter[reference_code]` with a representative message +
  class per code.
- Add a `newly_sent` view (successes whose **initial** target msg_id was `None`) so the
  reconcile/SEND post-run DB write records only new `MirroredMessage` pairs (§5a).

### 4. Scheduler rewrite (`KernelWorkControl.run_till_completion`)
Replace the "create all tasks + `aio.wait(ALL_COMPLETED)`" loop with a bounded worker pool
sharing a module-global `RateLimiter`:

```python
async def run_till_completion(self):
    async with kernel_work_control_registry.lock_source_message(self):
        kernel_work_control_registry.register(self)
        loop_number = 0
        while self.is_work_left_to_do and loop_number < self.retry_threshold:
            await self._run_batch(self.targets_to_schedule.items(),
                                  delay=0 if loop_number == 0 else None)
            loop_number += 1

async def _run_batch(self, batch, *, delay):
    sem = aio.Semaphore(cfg.mirror_max_concurrency)
    async def worker(ch_id, msg_id):
        self.report_scheduled(ch_id, msg_id)
        if delay is None:                          # retry sleep OUTSIDE the slot
            await aio.sleep(randint(cfg.mirror_retry_min, cfg.mirror_retry_max))
        async with sem:
            outcome = await self._kernel(ch_id, msg_id)   # rate-limited inside kernel
        self._apply_outcome(outcome)
    tasks = [aio.create_task(worker(c, m)) for c, m in batch]
    self._tasks = set(tasks)                        # replace each batch, don't grow
    await aio.gather(*tasks, return_exceptions=True)
    self._tasks.clear()
```

- Concurrency bounded by `cfg.mirror_max_concurrency`; rate bounded by the shared `RateLimiter`
  (used inside the kernel). Done tasks released each batch (fixes the `_tasks` leak).
- Retry sleep no longer blocks the round nor holds a concurrency slot.
- `cancel()` **gracefully drains**: set `self._cancelled` so `targets_to_schedule` returns empty,
  move not-yet-started targets to `cancelled`, and let in-flight workers finish their current API
  call and record their outcome. `run_till_completion` then returns normally, so the impl's
  post-run DB write persists the successes-so-far. (Avoids the hard-`task.cancel()` race where a
  dest is sent Discord-side but not recorded → double-send on later reconcile.) Preserves the
  `MirrorCancel` command + button.
- Keep the per-source-message registry lock unchanged.

### 5. Rewrite the kernels (`(ch_id, msg_id) -> KernelOutcome`)
For all kernels:
- Do the API work inside `async with rate_limiter:` (the module-global `RateLimiter`).
- On success `return KernelSuccess(...)`; on exception
  `return KernelFailure(ch_id, e, classify_error(e), reference_code(identity_for_exc(e)))`.
- **Remove** the per-channel `logging.exception` and the `report_*` side-effect calls (now done
  centrally by `_apply_outcome`).
- SEND kernel: keep crosspost as non-fatal post-success work; keep the "already been
  crossposted" special case as success/ignore.
- DELETE kernel: unchanged in shape.

### 5a. Reconcile model for edits (`message_update_repeater_impl`)
Turn UPDATE into a **reconcile** so all destinations converge on the source's current content:
- Compute desired dests: `MirroredChannel.fetch_dests(msg.channel_id)` minus the source channel
  (as SEND does).
- Compute existing dest messages: `MirroredMessage.get_dest_msgs_and_channels(msg.id)` →
  `{channel_id: dest_msg_id}`.
- Build the target map: each desired dest gets `dest_msg_id` if it has one, else `None`.
- Use a **reconcile kernel** that branches on `msg_id`: **edit** when `msg_id is not None`
  (existing edit logic), **send** when `msg_id is None` (reuse the SEND kernel's send + crosspost
  path). `KernelSuccess.message_id` is the existing id for edits and the newly-created id for sends.
- Post-run DB write: record **only the new pairs** (channels whose target started as `None` and
  succeeded) via `MirroredMessage.add_msgs_in_batch` — edited channels already have their pair.
  Track these via the tracker's `newly_sent` view.
- (Out of scope, note only) channels that have a stale mirrored message but are *no longer* a
  desired dest are not deleted here; convergence currently means "every desired dest matches",
  not "prune undesired dests".

### 6. Rate-limit + retry config (`dd/common/cfg.py`)
Add via the existing `_getenv` pattern:
- `mirror_max_concurrency` (int, default **8**) — worker-pool concurrency cap.
- `mirror_rate_per_sec` (float, default **30**) — global token-bucket rate (parse as float like
  `mirror_failure_ratio_threshold`).
- `mirror_retry_min` / `mirror_retry_max` (int, **180** / **300**) — replace the literal
  `randint(180,300)`.

One module-global `RateLimiter(cfg.mirror_rate_per_sec)` shared across all mirror runs/op types.
Also add the new vars to `.env-example` if required vars are documented there.

### 7. Progress logging cleanup + Components V2 (`mirror.py`)
- Add `render_mirror_progress(tracker, *, title, source_links, start_time, status,
  enable_cancellation) -> list[h.api.ComponentBuilder]` that builds sections (source message
  link, source channel link, ✅ Completed / 🔁 Retrying / ❌ Failed / ⏳ Remaining counts,
  elapsed via `format_duration`, time-to-first-pass, footer status, and a **failure breakdown**
  of `ref_code ×count` for the top codes) and calls `build_container(sections,
  accent_color=cfg.embed_error_color if tracker.failed_targets else default)`. **Drop the
  thumbnail.**
- Collapse the two progress functions into:
  - `start_progress_logger(...)` — render, `channel.send(components=..., flags=IS_COMPONENTS_V2)`,
    optionally attach the cancel menu, spawn the update loop, return.
  - `_update_progress_loop(message, tracker, ...)` — every 5 s re-render from
    `render_mirror_progress` and `message.edit(components=..., flags=IS_COMPONENTS_V2)`.
    **Eliminates the magic field indices** since we re-render rather than mutate fields. Replace
    `5**tries` backoff with `min(60, 5*tries)`.

### 8. Cancel button → lightbulb components (SEND + UPDATE ops)
Mirror the `Paginator` pattern:
- Build a `lbc.Menu` with one DANGER `add_interactive_button(on_cancel,
  custom_id=f"dd_mirror_cancel:{source_message_id}", label="Cancel Mirror")`.
- Render a visually-identical button (same `custom_id`) inside the container via
  `container.add_action_row([...])`.
- `menu.attach_persistent(client, timeout=...)` (background, non-blocking).
- `on_cancel`: owner check via `bot.fetch_owner_ids()`, `control.cancel()` (graceful drain per
  §4), edit the message to a disabled/cancelled state, stop the menu handle. The impl's post-run
  DB write then persists successes-so-far so a later edit reconciles correctly.
- **Enable for SEND and UPDATE** (`enable_cancellation=True` for both; DELETE stays off). Relax
  `KernelWorkControlRegistry.cancel` to permit SEND + UPDATE (not UPDATE-only), which also makes
  the `MirrorCancel` admin command work for sends.
- **Namespace `custom_id` by `source_message_id`** — the shared-client router would otherwise
  cross-fire between concurrent progress messages (correctness requirement).
- Thread `client: lb.Client = lb.di.INJECTED` from the listeners → `*_repeater_impl` → progress
  logger. Remove `LogCancelButton` and miru usage from this path. Leave `miru.install` in
  `__main__.py` unless a grep confirms nothing else uses it.

### 9. One aggregated failure alert per run
- After `run_till_completion`, build `failure_breakdown` and emit **one** `health_logger` record
  summarizing `{ref_code: (count, sample_message, PERMANENT/TRANSIENT)}`.
- Severity: route through the existing `flag_mirror_failure_ratio` ratio logic (CRITICAL on
  majority-fail per `cfg.mirror_failure_ratio_threshold`), else ERROR if any permanent failures,
  else nothing. The existing `DiscordLogHandler` dedups/renders it (CV2) and pings owners on
  CRITICAL. Enrich `flag_mirror_failure_ratio` to consume `_failures` for a richer message.
  Replaces N per-channel alerts with 1 grouped, ref-coded one.

---

## Suggested implementation order
1. `utils.py`: move `identity_for_exc`/`reference_code` (re-import in `discord_logging.py`); add
   `format_duration`, `ErrorClass`, `classify_error`. Lowest-risk, unblocks everything, fully
   unit-testable.
2. New `mirror_core.py`: outcome/kernel types, `RateLimiter`, and the moved tracker/control/
   registry classes (so they're testable without the listener module's DB/bot imports).
3. Tracker changes (`_permanently_failed`, `_failures`, `_cancelled`, `_apply_outcome`,
   `failure_breakdown`, `newly_sent`; predicate updates).
4. Scheduler rewrite (worker pool + shared `RateLimiter`; graceful-drain `cancel()`; fix `_tasks`).
5. Rewrite the kernels to outcome-returning; make UPDATE a reconcile (§5a).
6. `cfg.py` additions (+ `.env-example`).
7. Progress logging: `render_mirror_progress`, collapse the two functions, drop magic indices/
   thumbnail, fix backoff.
8. Cancel button → lightbulb components for SEND + UPDATE; thread `client`; relax registry
   restriction; remove `LogCancelButton`/miru from this path.
9. Aggregated failure alert; enrich `flag_mirror_failure_ratio`.

## Critical files
- `dd/beacon/extensions/mirror.py` — kernels, scheduler, progress logging, cancel button, alert.
- `dd/beacon/mirror_core.py` — **new**: tracker/control/registry, kernel types, `RateLimiter`.
- `dd/common/utils.py` — moved `identity_for_exc`/`reference_code`; new `format_duration`,
  `ErrorClass`, `classify_error`.
- `dd/common/discord_logging.py` — re-import moved helpers (no behavior change).
- `dd/common/cfg.py` — new mirror concurrency/rate/retry settings.
- `dd/common/components.py` — reuse `build_container`; no change expected.
- `dd/beacon/extensions/mirror_tracing.py` — keep `TimedSemaphore` import working.

## Verification
- **Unit tests** (`dd/beacon/tests/`, pytest + pytest-asyncio; logic lives in `mirror_core.py`
  so no live bot needed):
  - `test_classify_error.py` — parametrized hikari exceptions (403/404 w/ codes, 5xx, 429,
    `TimeoutError`, plain `ValueError`) → expected `ErrorClass`; `reference_code` determinism.
  - `test_format_duration.py` — sub-minute / exactly 60 s / multi-minute.
  - `test_rate_limiter.py` — N acquisitions take `>= (N-1)/rate`; concurrency bound separate.
  - `test_tracker.py` — `_apply_outcome` sequences: permanent excluded from `targets_to_schedule`
    immediately, transient retried to threshold, breakdown counts by `reference_code`.
  - `test_run_till_completion.py` — fake `MirrorKernel`+`RateLimiter`: max concurrency bounded,
    `_tasks` doesn't grow, permanent-no-retry, transient-retry-give-up, and `cancel()` gracefully
    drains (no new targets scheduled, in-flight finish & record, returns normally).
  - `test_reconcile.py` — given desired dests + a partial `get_dest_msgs_and_channels` map,
    assert the reconcile target map sends to missing dests (`msg_id=None`) and edits existing
    ones, and that `newly_sent` contains exactly the missing dests that succeeded.
  - `test_render_mirror_progress.py` — known tracker state → `ContainerComponentBuilder` with
    expected sections/accent (error color when failures), cancel row for SEND + UPDATE (not DELETE).
- `uv run python -m pytest dd/beacon/tests/` (+ `uv run ruff check`, `uv run ty check`).
  Note: pytest needs a live MySQL DB and may import the sibling v2 repo via the `dd` namespace —
  the new unit tests are designed to avoid the DB by living in `mirror_core.py`.
- **Manual** (`uv run python -OOm dd.beacon` / `make run-beacon-local` with populated `.env`):
  post a message in a source channel, watch the CV2 progress message update; point a mirror at a
  channel the bot can't post to → confirm it's marked permanent (no retries) and appears in the
  failure breakdown; confirm interactive commands stay responsive during a large fan-out (rate
  headroom).
- **Reconcile/convergence scenario** (the core new behavior): start a SEND, **cancel it mid-run**
  via the button → verify `MirroredMessage` records only the dests that actually received it; then
  **edit the source message** → verify the dests that have it get edited and the dests that were
  missed get a fresh send, leaving every destination matching the source.

## Risks / watch-outs
- `custom_id` collision: shared-client menu routing means two concurrent progress messages must
  namespace the cancel `custom_id` by `source_message_id`, or presses cross-fire. Real bug if missed.
- Retry sleep placement (inside vs outside the concurrency slot) materially changes retry-phase
  throughput — keep it outside the slot as decided.
- Verify hikari 2.5 exception attribute names (`.code`, `.status` on `HTTPResponseError`) before
  finalizing `classify_error`.
- Cancel race: prefer graceful drain over hard `task.cancel()` so a sent-but-unrecorded dest
  can't cause a double-send on reconcile.
- 30/s assumes spare global budget; if other subsystems burst, tune down (e.g. 25/s).
- Don't break `mirror_tracing.py`'s `TimedSemaphore` import when extracting code.

## Memory-leak addendum (from project-wide leak audit; plan only)

A leak audit found that the mirror subsystem accumulates **per-source-message** state that is
never released after a normal (non-cancelled) operation. The §4 scheduler rewrite already
fixes the `_tasks` set, but the **registry entry and the lock are not released** — §4 says
"keep the per-source-message registry lock unchanged", which leaves the two biggest leaks in
place. Fold the teardown below into §4.

Root cause: `run_till_completion` calls `kernel_work_control_registry.register(self)` and
acquires `lock_source_message(self)`, but only `cancel()` (UPDATE-only) ever removes anything.
Every distinct `(src_ch_id, src_msg_id)` — i.e. effectively every announcement ever mirrored —
leaves a `KernelWorkControl` (+ its tracker dicts and kernel closure that captures the source
message and all targets) and an `aio.Lock` pinned for the process lifetime.

| ID | Leak | Symbol (grep) | Status under current plan |
|----|------|---------------|---------------------------|
| **M1** | registry entry never removed after completion | `KernelWorkControlRegistry._registry` / `register` | **NOT fixed** — add teardown |
| **M2** | task set grows across retry rounds | `KernelWorkControl._tasks` | already fixed (§4 `_tasks = set(tasks)` / `.clear()`) |
| **M3** | per-message lock never removed | `KernelWorkControlRegistry._locks` (`defaultdict(aio.Lock)`) | **NOT fixed** — line "keep registry lock unchanged" leaves it leaking |
| **M4** | traced dests never removed (also stale vs DB) | `mirror_tracing.non_legacy_mirrors` (`defaultdict(list)`) | not in this plan — add removal |
| **M5** | legacy src cache never shrinks (intentional) | `MirroredChannel._legacy_srcs_cache` (`schemas.py`) | LOW / by design — leave |

Remediation direction (integrate into §4's `run_till_completion`):
- Wrap the body in `try/finally`; in `finally`, after the op is done, **remove the registry
  entry** for the key. Because `register()` already rejects a second op for the same key while
  work is in progress (`is_work_left_to_do`), there is no competing in-flight op for that key,
  so popping on completion is safe.
- **Evict the lock** for the key too, but only when it is not held/awaited — e.g. inside the
  registry, drop `_locks[key]` after releasing it iff `not lock.locked()` (or switch `_locks`
  from a `defaultdict` to a small ref-counted map). Avoid deleting a lock another coroutine is
  awaiting. Since same-key ops are serialized/rejected, contention is rare, but guard anyway.
- **M4**: when a non-legacy mirror is disabled/removed, remove its dest from
  `non_legacy_mirrors[src]` (and drop the key when its list empties), or re-derive the entry
  from the DB. This fixes both the leak and the **staleness** bug (the `message_tracer` gate
  `... in non_legacy_mirrors` can disagree with the DB after a mirror is disabled).

Verification (add to §-tests): a unit test that runs `KernelWorkControl.run_till_completion`
against a stub kernel and asserts `kernel_work_control_registry._registry`, `._locks`, and the
control's `_tasks` are **all empty** afterward (these assertions fail today — that *is* the
leak). Lives in `mirror_core.py`-backed tests so it needs no DB.
