# Plan: Mirror fan-out rewrite — durable delivery ledger (`mirror-v2`)

**Audience:** an Opus executor agent with NO prior session context. Everything needed is
in this document plus the repo. Step 1 commits this plan as
`plans/mirror_v2_delivery_ledger.md` on the `mirror-v2` branch (repo convention: remove
it when fully executed; prompt the owner if partially executed).

## 1. Context

Repo `dd` (Python 3.13, SQLAlchemy 2.0 async + MySQL/asyncmy, Atlas migrations,
hikari/lightbulb v3, uv, ruff line-length 88, `ty`, pytest). The `dd.beacon` bot's
**mirror subsystem** fans one source-channel message out to N destination channels
("legacy" repost mirrors; `legacy=True` rows in `mirrored_channel`). Non-legacy rows are
Discord-native channel follows — **completely unaffected by this rewrite**.

**Read before coding:** `CLAUDE.md`, `docs/architecture.md`. Key rules: `uv run`
everything; tests via `make test` (loads `.env`); `make check` green before merge;
conventional commits; **never push `shark/main`** (prod deploy); dev deploys only with
explicit owner confirmation; no `dd/__init__.py` (implicit namespace packages); DB via
the `db_session` proxy / `@ensure_session(db_session)` pattern only.

**What's being replaced and why.** Today's orchestration is in-memory and ephemeral:
`dd/beacon/mirror_core.py` (652 lines) holds `KernelWorkTracker`/`KernelWorkControl`
(per-run accounting), `KernelWorkControlRegistry` (per-source refcounted asyncio locks,
cancel/supersede-by-edit graceful drain), retries as `aio.sleep(randint(180,300))` in
live tasks. `dd/beacon/extensions/mirror.py` (~1600 lines) persists **only after a whole
run** (`MirroredMessage.add_msgs_in_batch`, strike columns on `MirroredChannel`, then a
`disable_legacy_failing_mirrors()` sweep). A crash mid-run loses all in-flight state; the
create/edit double-send race is closed by the subtlest lock code in the repo; and dest
health is a hand-maintained aggregate (`legacy_disable_strikes`/`legacy_failing_since`,
strikes only for perm-probe-confirmed-dead dests, disable at ≥3 strikes AND ≥48h streak).

**Target:** a durable `mirror_delivery` ledger (one row per `(src_msg_id, dest_ch_id)`
carrying desired/applied version + state), thin transactional gateway handlers, one
per-process convergence worker claiming batches via `SELECT … FOR UPDATE SKIP LOCKED`, a
continuous write-back flusher, auto-disable as a derived streak query, and
`mirror_delivery` **subsuming `mirrored_message`** entirely.

### Owner decisions (final — do not relitigate)

1. Write-back sync: **continuous flusher** (no timer; write whatever accrued as soon as
   the previous batched write returns).
2. Multi-bot: **claim-level safety only** (`FOR UPDATE SKIP LOCKED` + `claimed_by`/
   `claimed_at`, stale-claim timeout). No further coordination.
3. Strike columns `legacy_disable_strikes`/`legacy_failing_since`: **dropped**; health is
   derived from the ledger. `enabled` + `legacy_disable_for_failure_on_date` stay.
4. Branches: snapshot current code as **`mirror-v1`**, rewrite on **`mirror-v2`** forked
   off it (§2).
5. Crash duplicate-send window: **accepted** (≤ one flush of outcomes at risk, recovered
   after the stale-claim timeout; strictly better than today's whole-run loss).
6. Retention: non-terminal rows prune at **21 days**; terminal FAILED rows at **90 days**
   (disable evidence for low-cadence sources).
7. Post-restart backlog: **post a recovery progress card** (synthetic RunViews, §5.11) —
   not silent.
8. Single terminal `FAILED` state with `last_error_class` distinguishing PERMANENT vs
   exhausted-TRANSIENT (not two states); the disable query filters on `confirmed_dead`.

### Requirements checklist (owner's 10 asks → where satisfied)

1. Same Discord logging channels — progress UI stays on `cfg.log_channel`, alerts on
   `cfg.alerts_channel` (§6.1, §6.3).
2. Critical alert for significant portion failing — `flag_mirror_failure_ratio`
   semantics unchanged (§6.2).
3. Errors grouped, not spam — per-run `failure_breakdown` grouping + untouched
   `discord_logging` batching/dedup (§6.2, §6.3).
4. Disabled-channel count in the progress log — NEW final-render line (§6.1).
5. Keep "Time taken"; DROP "Time to try all channels once" (§6.1).
6. Same ETA + channels/sec formulas (§6.1).
7. Rest of logging free to redesign — worker logs redesigned (§5, §6.5).
8. Best-effort DB sync of progress, fast — continuous flusher (§5.5).
9. Biggest servers first — preserved at claim time (§5.4).
10. Multi-bot safety stretch — claim-level SKIP LOCKED (§5.4).

### Core invariants (enforce in code + docstrings)

1. **The ledger stores intent, not content.** Content is fetched fresh from Discord at
   delivery time. An edit bumps `desired_version`; the worker converges rows where
   `applied_version < desired_version`.
2. **Delivery coroutines never await the DB; the flusher never awaits Discord.**
3. **A dest message id, once created Discord-side and observed, is always recorded** —
   even when the version guard fails (row returns to PENDING *with* `dest_msg_id`, so
   re-convergence edits instead of re-sending).
4. **Dest ordering is biggest-server-first** — LEFT JOIN `server_statistics`,
   `ORDER BY DESC(COALESCE(population, 10**12))` (exact same coalesce as today's
   `MirroredChannel.fetch_dests`), applied at claim time.
5. **Disable granularity is per `(src_ch_id, dest_ch_id)` pair** (same as today's
   per-mirror-row strikes).

## 2. Branch workflow

```
git branch mirror-v1 dev            # named snapshot of the existing implementation
git push origin mirror-v1
git checkout -b mirror-v2 mirror-v1 # all rewrite work here
git push -u origin mirror-v2
```
Merge `mirror-v2 → dev` only when `make check` is green. Never push `shark/main`.
Deploying dev requires explicit owner confirmation (docker-entrypoint auto-applies the
migration via `atlas migrate apply` on deploy).

## 3. Target schema

### 3.1 New model in `dd/common/schemas.py`

Match the file's existing style (plain `Column`, `@classmethod` + `@ensure_session(db_session)`).

```python
class DeliveryState(enum.StrEnum):
    PENDING = "PENDING"       # needs work (applied < desired, or unapplied delete)
    CLAIMED = "CLAIMED"       # claimed by a worker (claimed_by/claimed_at set)
    DELIVERED = "DELIVERED"   # converged
    FAILED = "FAILED"         # terminal (last_error_class: PERMANENT or exhausted TRANSIENT)
    CANCELLED = "CANCELLED"   # user cancel / delete-before-delivery / undo neutralisation


class MirrorDelivery(Base):
    """Durable delivery ledger: one row per (source message, destination channel).

    Subsumes MirroredMessage. Stores *intent* (desired_version / deleted), never
    content — content is fetched fresh from Discord at delivery time.
    """
    __tablename__ = "mirror_delivery"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        Index("ix_mirror_delivery_state_due", "state", "due_at"),          # claim scan
        Index("ix_mirror_delivery_pair_state", "src_ch_id", "dest_ch_id", "state"),  # streak query
        Index("ix_mirror_delivery_created_at", "created_at"),               # prune
    )

    src_msg_id = Column(BigInteger, primary_key=True)
    dest_ch_id = Column(BigInteger, primary_key=True)
    src_ch_id = Column(BigInteger, nullable=False)
    dest_server_id = Column(BigInteger, nullable=True)   # denormalised for claim-order JOIN
    dest_msg_id = Column(BigInteger, nullable=True)      # NULL until first delivery
    desired_version = Column(Integer, nullable=False, default=1)
    applied_version = Column(Integer, nullable=False, default=0)
    deleted = Column(Boolean, nullable=False, default=False)   # delete-intent flag
    state = Column(String(16), nullable=False, default=DeliveryState.PENDING.value)
    attempts = Column(Integer, nullable=False, default=0)
    due_at = Column(DateTime, nullable=False, default=<utcnow>)
    claimed_by = Column(String(64), nullable=True)
    claimed_at = Column(DateTime, nullable=True)
    last_error_ref = Column(String(8), nullable=True)
    last_error_class = Column(String(12), nullable=True)   # "PERMANENT"/"TRANSIENT"
    last_error_msg = Column(String(256), nullable=True)     # truncated sample
    confirmed_dead = Column(Boolean, nullable=False, default=False)  # perm-probe verdict
    created_at = Column(DateTime, nullable=False, default=<utcnow>)
    finished_at = Column(DateTime, nullable=True)   # set on DELIVERED/FAILED/CANCELLED
```

State semantics: a `DELIVERED` row whose `desired_version` is bumped returns to
`PENDING` (edit reconcile). `FAILED` and `CANCELLED` rows are also reset to `PENDING` by
an edit bump (matches today's reconcile fresh-send) — except rows with `deleted=1`,
which an edit never touches. Retry policy: flat jittered backoff
`randint(cfg.mirror_retry_min, cfg.mirror_retry_max)` seconds (matches today); max
attempts 3 when the row is an initial send (`applied_version == 0 and not deleted`),
else 2 — preserving today's per-op thresholds. Exhaustion or PERMANENT → `FAILED`.

Add a small dialect helper next to the model:

```python
def _insert_ignore(cls):
    """Duplicate-PK-ignoring INSERT, portable: MySQL ``INSERT IGNORE`` /
    SQLite ``INSERT OR IGNORE`` via prefix_with(..., dialect=...)."""
    return insert(cls).prefix_with("IGNORE", dialect="mysql").prefix_with("OR IGNORE", dialect="sqlite")
```

### 3.2 Hand-authored migration

The `atlas` CLI is available at `/usr/local/bin/atlas` (community v1.2.4); Docker is NOT
— so hand-author the SQL (as `migrations/20260708174309.sql` / `20260708183809.sql`
were) and run `atlas migrate hash` to update `atlas.sum`. Never hand-edit `atlas.sum`.

Create `migrations/$(date -u +%Y%m%d%H%M%S).sql`:

```sql
-- Create "mirror_delivery" table
CREATE TABLE `mirror_delivery` (
  `src_msg_id` bigint NOT NULL,
  `dest_ch_id` bigint NOT NULL,
  `src_ch_id` bigint NOT NULL,
  `dest_server_id` bigint NULL,
  `dest_msg_id` bigint NULL,
  `desired_version` int NOT NULL,
  `applied_version` int NOT NULL,
  `deleted` bool NOT NULL,
  `state` varchar(16) NOT NULL,
  `attempts` int NOT NULL,
  `due_at` datetime NOT NULL,
  `claimed_by` varchar(64) NULL,
  `claimed_at` datetime NULL,
  `last_error_ref` varchar(8) NULL,
  `last_error_class` varchar(12) NULL,
  `last_error_msg` varchar(256) NULL,
  `confirmed_dead` bool NOT NULL,
  `created_at` datetime NOT NULL,
  `finished_at` datetime NULL,
  PRIMARY KEY (`src_msg_id`, `dest_ch_id`),
  INDEX `ix_mirror_delivery_state_due` (`state`, `due_at`),
  INDEX `ix_mirror_delivery_pair_state` (`src_ch_id`, `dest_ch_id`, `state`),
  INDEX `ix_mirror_delivery_created_at` (`created_at`)
) CHARSET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
-- Backfill from mirrored_message (DELIVERED, applied=desired=1). GROUP BY dedupes
-- duplicate (source_msg, dest_ch) pairs; MAX(dest_msg) picks the newest dest message
-- (snowflakes are time-ordered), matching old build_reconcile_targets last-wins.
INSERT INTO `mirror_delivery`
  (`src_msg_id`, `dest_ch_id`, `src_ch_id`, `dest_server_id`, `dest_msg_id`,
   `desired_version`, `applied_version`, `deleted`, `state`, `attempts`, `due_at`,
   `confirmed_dead`, `created_at`, `finished_at`)
SELECT mm.`source_msg`, mm.`dest_ch`, MAX(mm.`src_ch`), MAX(mc.`dest_server_id`),
       MAX(mm.`dest_msg`), 1, 1, 0, 'DELIVERED', 1,
       MAX(mm.`creation_datetime`), 0, MAX(mm.`creation_datetime`),
       MAX(mm.`creation_datetime`)
FROM `mirrored_message` mm
LEFT JOIN `mirrored_channel` mc
  ON mc.`src_id` = mm.`src_ch` AND mc.`dest_id` = mm.`dest_ch`
GROUP BY mm.`source_msg`, mm.`dest_ch`;
-- Drop "mirrored_message" (subsumed by the ledger)
DROP TABLE `mirrored_message`;
-- Strike columns replaced by the derived streak query
ALTER TABLE `mirrored_channel` DROP COLUMN `legacy_disable_strikes`, DROP COLUMN `legacy_failing_since`;
```

In the **same change**, `schemas.py` must gain `MirrorDelivery` and lose
`MirroredMessage` + the two strike columns, so the Atlas desired-state
(`python dd/common/schemas.py --print-ddl` per `atlas.hcl`) matches the migration
end-state. `legacy_disable_for_failure_on_date` and `enabled` stay.

## 4. Module layout

- **`dd/common/schemas.py`** — gains `DeliveryState`, `MirrorDelivery` + methods (§5),
  `_insert_ignore`, outcome dataclasses (defined here to keep the dependency direction
  clean — beacon imports common, never the reverse); replaces
  `disable_legacy_failing_mirrors` with `disable_failing_mirrors` (§5.6); adapts
  `undo_auto_disable_for_failure` (§5.7); **deletes** `MirroredMessage`, strike columns,
  `clear_mirror_strikes_in_batch`, `add_confirmed_dead_strikes_in_batch`,
  `get_legacy_failing_mirrors`.
- **`dd/beacon/mirror_core.py`** — shrinks to the pure survivors: `RateLimiter` +
  `rate_limiter`, `MirrorOperationType`, `FailureGroup`, and the new **`RunView`**
  (§5.9). **Deletes** `KernelWorkTracker`, `KernelWorkControl`,
  `KernelWorkControlRegistry`, `kernel_work_control_registry`,
  `build_reconcile_targets`, `KernelSuccess`/`KernelFailure`/`MirrorKernel`.
- **`dd/beacon/mirror_worker.py`** (new) — the convergence worker: wake
  `asyncio.Event`, claim loop, per-source-message delivery groups (semaphore + shared
  rate limiter), perm probe, outcome buffer, flusher coroutine, run-view registry,
  run-end hook, startup backlog recovery. Owns lifecycle (`start(bot)`/`stop()`/
  `nudge()`). `_send_one`, `edit_one`, `add_role_ping_to_msg`, `_cv2_components_for`,
  `_is_cv2` move here verbatim from `mirror.py` (delivery happens here now).
- **`dd/beacon/extensions/mirror.py`** — keeps: listeners (now thin enqueue + nudge),
  crosspost wait, `is_content_edit` gate, `ignore_non_src_channels`, progress UI
  (`start_progress_logger`/`render_mirror_progress` retargeted to `RunView`), cancel
  menu, `flag_mirror_failure_ratio`, `health_logger`, admin commands,
  `refresh_server_sizes`, `prune_message_db` (→ `MirrorDelivery.prune`). A
  `StartedEvent` listener starts the worker. `_confirm_dead_dests` is deleted (probe
  moves into the worker).

## 5. Data flows — implement exactly this

### 5.1 Enqueue: CREATE (`message_create_repeater_impl`, now small)

Keep the crosspost wait and message re-fetch. Then one transaction — a single
`INSERT … SELECT` (no read-then-write, no locks):

```python
@classmethod
@ensure_session(db_session)
async def enqueue_send(cls, src_ch_id, src_msg_id, *, session=_UNSET) -> int:
    now = <utcnow>
    sel = select(
        literal(int(src_msg_id)), MirroredChannel.dest_id, literal(int(src_ch_id)),
        MirroredChannel.dest_server_id, literal(1), literal(0), literal(False),
        literal(DeliveryState.PENDING.value), literal(0), literal(now),
        literal(False), literal(now),
    ).where(and_(
        MirroredChannel.src_id == int(src_ch_id),
        MirroredChannel.legacy, MirroredChannel.enabled,
        MirroredChannel.dest_id != int(src_ch_id),
    ))
    result = await session.execute(_insert_ignore(cls).from_select([...cols...], sel))
    return result.rowcount or 0
```

INSERT-IGNORE makes duplicate gateway events and a manual `mirror_send` on an
already-mirrored message idempotent (improvement over today). After commit: register
`RunView(op=SEND, total=<count of non-terminal rows for src_msg>)`, start the progress
logger, `mirror_worker.nudge()`.

### 5.2 Enqueue: EDIT (`message_update_repeater_impl`)

Keep the `is_content_edit` + CROSSPOSTED-flag gates. One transaction, two statements —
no cancel/supersede machinery:

```python
# 1) bump every non-deleted row back to pending at the new version
update(cls).where(and_(cls.src_msg_id == src_msg_id, ~cls.deleted)).values(
    desired_version=cls.desired_version + 1,
    state=DeliveryState.PENDING.value, attempts=0, due_at=now, finished_at=None)
# 2) reconcile: same INSERT…SELECT as enqueue_send for dests added since the send
#    (_insert_ignore no-ops existing rows)
```

Both rowcounts 0 → not a mirrored message, return silently. Rows for dests since
removed from `mirrored_channel` keep converging (today's `build_reconcile_targets`
setdefault behaviour). After commit: finalize any live `RunView` for this src_msg as
`superseded_by_edit` (progress card renders "♻️ Superseded by edit"), register a fresh
`RunView(op=UPDATE)`, progress logger, `nudge()`.

### 5.3 Enqueue: DELETE (`message_delete_repeater_impl`)

One statement, keyed on `src_msg_id` only (src channel unknown, as today):

```python
update(cls).where(and_(cls.src_msg_id == src_msg_id,
                       cls.state != DeliveryState.CANCELLED.value)).values(
    deleted=True,
    state=case((cls.dest_msg_id.is_(None), DeliveryState.CANCELLED.value),
               else_=DeliveryState.PENDING.value),
    attempts=0, due_at=now, finished_at=None)
```

Never-delivered rows → `CANCELLED` (nothing to delete Discord-side); delivered rows →
`PENDING` with delete-intent. A delete racing an in-flight send resolves via the
flusher's guard (§5.5): the send's success write-back sees `deleted=1`, records
`dest_msg_id`, returns the row to `PENDING`; the worker then deletes the just-sent
message. rowcount 0 → not mirrored, return. Else `RunView(op=DELETE)` (no cancel
button, as today), progress logger, `nudge()`.

### 5.4 Claim (first `FOR UPDATE SKIP LOCKED` in the codebase; READ COMMITTED is fine)

`MirrorDelivery.claim_batch(worker_id, batch_size, stale_cutoff, *, session)` — one txn:

```python
rows = (await session.execute(
    select(cls)
    .join(ServerStatistics, cls.dest_server_id == ServerStatistics.id, isouter=True)
    .where(or_(
        and_(cls.state == DeliveryState.PENDING.value, cls.due_at <= now),
        # stale-claim recovery: a worker that died mid-batch leaves CLAIMED rows
        and_(cls.state == DeliveryState.CLAIMED.value, cls.claimed_at <= stale_cutoff),
    ))
    .order_by(desc(coalesce(ServerStatistics.population, 10**12)), cls.created_at)
    .limit(batch_size)
    .with_for_update(skip_locked=True, of=cls)
)).scalars().all()
# then one UPDATE setting state=CLAIMED, claimed_by, claimed_at for the claimed PKs
# (tuple_(src_msg_id, dest_ch_id).in_(pairs); fallback: executemany per-pair)
return [ClaimedRow(...) for r in rows]   # frozen dataclass snapshots, not ORM objects
```

`worker_id = f"{socket.gethostname()}:{os.getpid()}"[:64]`. SQLite note: SQLAlchemy's
SQLite dialect silently omits `FOR UPDATE`, so this method runs unmodified in the
SQLite suite (single process, no contention); SKIP LOCKED concurrency gets a
MySQL-gated test (§9).

### 5.5 Deliver + flush (`dd/beacon/mirror_worker.py`)

Worker main loop (one per bot process, started from `StartedEvent`):

```
while running:
    wake.clear()
    batch = await MirrorDelivery.claim_batch(...)            # DB only
    if not batch:
        with contextlib.suppress(TimeoutError):
            await aio.wait_for(wake.wait(), timeout=cfg.mirror_poll_interval)
        continue
    await process(batch)                                      # Discord only + buffer
```

`process(batch)`: group rows by `src_msg_id`. Per group:

1. If all rows are `deleted` → skip content fetch. Else fetch the source message ONCE
   (`bot.rest.fetch_message`) + `MirroredChannel.fetch_mirror_and_role_mention_id`
   once; apply `utils.filter_discord_autoembeds`. Source-fetch TRANSIENT failure →
   transient outcomes for the whole group; PERMANENT (source gone, e.g. missed delete)
   → cancelled outcomes + `health_logger.warning`.
2. Per row, a delivery task under `aio.Semaphore(cfg.mirror_max_concurrency)`; every
   Discord call goes through the shared `rate_limiter`. Before starting a row, check
   the group's `RunView.cancel_requested` → cancelled outcome without touching Discord.
   Op per row: `deleted and dest_msg_id` → fetch + delete (dest-msg `NotFoundError`
   counts as success); `dest_msg_id is None` → `_send_one` (send + crosspost-if-news,
   "already crossposted" 400 tolerated); else → `edit_one` (content/embeds/attachments
   or CV2 components — verbatim from today).
3. On exception: `classify_error`. PERMANENT, or TRANSIENT with `attempts+1 >=` cap
   (3 send / 2 edit-delete) → terminal: if PERMANENT and `cfg.disable_bad_channels`,
   run `utils.confirm_dest_unsendable(bot, dest_ch_id)` here (never raises out; probe
   error/UNKNOWN/SENDABLE → `confirmed_dead=False`, keep today's bias + the per-run
   aggregated "failed permanently but not confirmed dead" warning via the RunView).
   TRANSIENT below cap → transient outcome with
   `due_at = now + randint(cfg.mirror_retry_min, cfg.mirror_retry_max)` seconds —
   retries no longer hold worker slots or in-process sleeps.
4. Every outcome updates the group's `RunView` (pure memory) and is appended to the
   flusher buffer + `buffer_event.set()`.

**Flusher** (dedicated coroutine — the owner's explicit design):

```
while running:
    await buffer_event.wait(); buffer_event.clear()
    batch, buffer = buffer, []           # swap; new outcomes accrue during the write
    try:
        await MirrorDelivery.flush_outcomes(batch)   # one txn, batched
    except Exception:
        buffer[0:0] = batch              # re-queue at front
        log; await aio.sleep(backoff)    # 5s → capped 60s; buffer_event.set()
```

No timer: sync lag is one DB round-trip. `flush_outcomes` uses executemany per outcome
kind with a **static SQL shape** — the version/deleted guard is a CASE inside VALUES:

- **Success (send/edit)** — invariant 3: always record `dest_msg_id`,
  `applied_version`, clear claim/error fields; `state = CASE(desired_version ==
  :version AND NOT deleted → DELIVERED, else → PENDING)`; `finished_at` guarded the
  same way.
- **Delete-success**: `state=DELIVERED`, `applied_version=:version`, `finished_at=now`
  unconditionally (deleted is never un-set; edits skip deleted rows). Keep
  `dest_msg_id` for audit.
- **Transient failure**: `attempts=:n`, `due_at=:backoff`, `state=PENDING`, clear
  claim, set error fields. (The backoff `due_at` deliberately wins over an edit's
  `due_at=now` — the dest just failed; retrying the new version immediately would
  likely fail too.)
- **Terminal failure**: set error fields + `confirmed_dead=:dead`;
  `state = CASE(guard → FAILED, else → PENDING)` (an edit/delete raced us: keep
  converging); `finished_at` guarded.
- **Cancelled**: `state = CASE(guard → CANCELLED, else → PENDING)`, clear claim,
  `finished_at` guarded.

### 5.6 Auto-disable — the derived streak query (per `(src, dest)` pair)

Replace `MirroredChannel.disable_legacy_failing_mirrors` with
`MirroredChannel.disable_failing_mirrors(threshold=3)`:

```python
cutoff = now - timedelta(hours=cfg.mirror_disable_forgiveness_hours)
md = MirrorDelivery
last_ok = (select(md.src_ch_id.label("s"), md.dest_ch_id.label("d"),
                  func.max(md.finished_at).label("last_ok"))
           .where(md.state == DeliveryState.DELIVERED.value)
           .group_by(md.src_ch_id, md.dest_ch_id).subquery())
failing = (select(md.src_ch_id, md.dest_ch_id)
    .join(cls, and_(cls.src_id == md.src_ch_id, cls.dest_id == md.dest_ch_id,
                    cls.enabled, cls.legacy))
    .join(last_ok, and_(last_ok.c.s == md.src_ch_id, last_ok.c.d == md.dest_ch_id),
          isouter=True)
    .where(and_(md.state == DeliveryState.FAILED.value, md.confirmed_dead,
                # streak: only failures with no later success for this pair count
                or_(last_ok.c.last_ok.is_(None), md.finished_at > last_ok.c.last_ok)))
    .group_by(md.src_ch_id, md.dest_ch_id)
    .having(and_(func.count(func.distinct(md.src_msg_id)) >= threshold,
                 func.min(md.finished_at) <= cutoff)))
# then UPDATE mirrored_channel SET enabled=0, legacy_disable_for_failure_on_date=now
# WHERE enabled AND legacy AND (src_id, dest_id) IN pairs; return pairs
```

Correctness properties (all tested, §9): (a) any `DELIVERED` row for the pair after the
failures resets the streak (today's clear-on-success); (b) an edit that re-delivers a
previously-FAILED row flips it to DELIVERED — the "clear strikes on edit-recovery"
behaviour falls out for free; (c) pairs sharing a dest channel never cross-contaminate;
(d) `MIN(finished_at) <= cutoff` reproduces the `legacy_failing_since` 48h time gate.
Runs at run-end from the worker (§5.9), same cadence as today, gated on
`cfg.disable_bad_channels`.

### 5.7 undo_auto_disable

`MirroredChannel.undo_auto_disable_for_failure(since)` keeps its re-enable UPDATE
(`~enabled & legacy & legacy_disable_for_failure_on_date >= since`) but drops the
strike-column resets; instead, in the same transaction, neutralise the ledger evidence:

```python
update(MirrorDelivery).where(and_(
    tuple_(MirrorDelivery.src_ch_id, MirrorDelivery.dest_ch_id).in_(pairs),
    MirrorDelivery.state == DeliveryState.FAILED.value,
)).values(state=DeliveryState.CANCELLED.value, confirmed_dead=False)
```

CANCELLED counts as neither success nor failure in the streak query, so the pair won't
immediately re-disable. `/mirror undo_auto_disable` command body unchanged.

### 5.8 Prune + read replacements

- `MirrorDelivery.prune()`: `DELETE WHERE created_at < now-21d AND state != 'FAILED'`
  plus `DELETE WHERE created_at < now-90d` (owner decision 6). Daily `prune_message_db`
  task calls this.
- `MirroredMessage.get_dest_msgs_and_channels` has no remaining callers (delete is a
  ledger UPDATE; reconcile is inherent) — do not port it.

### 5.9 RunView + run-end hook (the minimal tracker survivor, in `mirror_core.py`)

```python
@dataclass
class RunView:
    op: MirrorOperationType
    src_ch_id: int | None
    src_msg_id: int
    total: int                      # non-terminal rows at registration
    start_time: float               # perf_counter()
    delivered: int = 0
    failed: int = 0                 # terminal failures
    cancelled_count: int = 0
    attempted_once: set[int]        # dest_ch_ids with >=1 attempt (drives "Retrying")
    failures: dict[int, RunFailure] # dest_ch -> (ref, class, sample, confirmed_dead)
    cancel_requested: bool = False
    superseded_by_edit: bool = False
    disabled_count: int = 0
    # properties: retrying, remaining, resolved, failure_breakdown (port the
    # Counter/most_common logic verbatim from KernelWorkTracker.failure_breakdown),
    # is_complete = delivered + failed + cancelled_count >= total (or superseded)
```

The worker holds `run_views: dict[int, RunView]` keyed by `src_msg_id`; handlers
register views; the worker records outcomes into the matching view. On completion the
worker spawns a run-end task (never blocks the claim loop): `flag_mirror_failure_ratio(view)`;
the aggregated not-confirmed-dead warning; if `cfg.disable_bad_channels` and op is
SEND/UPDATE → `disabled = await MirroredChannel.disable_failing_mirrors()`, set
`view.disabled_count`, emit the count-escalated sweep alert (unchanged thresholds
`_DISABLE_CRITICAL_MIN=10`/`_DISABLE_ERROR_MIN=5`: >10 critical, >5 error, else
warning); log the run-summary INFO line; then mark the view finished so the final
progress render includes "Disabled channels: N"; evict the view after the final render.

### 5.10 Cancel button / `mirror_cancel` command

```python
await MirrorDelivery.cancel_pending(src_msg_id)  # UPDATE → CANCELLED, finished_at=now
                                                 # WHERE src_msg_id=:id AND state='PENDING' AND deleted=0
view.cancel_requested = True                     # claimed-but-unstarted rows short-circuit
```

In-flight Discord calls drain and flush normally (guard-protected) — today's
graceful-drain semantics. Keep the owner-only lightbulb `Menu` wiring verbatim
(`dd_mirror_cancel:{src_msg_id}` custom id, `attach_persistent` 7h, `fetch_owner_ids`
check); only the callback body changes. `MirrorCancel`'s old registry errors become:
rowcount 0 and no live view → "no operation in progress". Cancel on SEND/UPDATE cards
only, not DELETE (unchanged).

### 5.11 Startup backlog recovery (owner decision 7: post a recovery card)

On `start(bot)`, before the main loop: query distinct `src_msg_id`s having non-terminal
rows (`state IN (PENDING, CLAIMED)`), with per-src counts and `src_ch_id`. For each,
register a synthetic `RunView` (op inferred: any `deleted` row → DELETE, else any
`applied_version == 0` row → SEND, else UPDATE; `total` = non-terminal row count;
`start_time = perf_counter()` now) and start a progress card titled
"Mirror recovery progress" (cancel enabled for SEND/UPDATE). Metrics are approximate by
design (elapsed restarts from recovery). Rows claimed later with no matching view still
process fine — log at INFO, no card.

## 6. Observability — requirement mapping

1. **Progress UI** (`cfg.log_channel`, 5s re-render loop, CV2 container — structure
   kept, retargeted from `KernelWorkControl` to `RunView`): title; source links;
   12-cell progress bar (`_progress_bar` math unchanged); Completed/Retrying/Failed/
   Remaining counts; **"Time taken: {elapsed}"** kept; **"Time to try all channels
   once" line DROPPED** (and `is_every_target_tried` with it). `_throughput_line`
   formulas exactly: `rate = (delivered+failed)/elapsed`,
   `"{rate:.1f} channels/sec · ETA ~{format_duration(remaining/rate)}"`,
   `remaining = total - resolved`, ETA dropped when remaining ≤ 0, omitted until first
   resolution. Failure breakdown: top 5 groups `` `{ref}` ×{count} ({class}) `` +
   "…and N more". **NEW: "Disabled channels: {N}"** on the final render when the
   run-end sweep disabled anything. Loop ends on `view.is_complete` (final render,
   stop menu handle).
2. **Failure-ratio alerts** — `flag_mirror_failure_ratio` semantics unchanged, computed
   from the RunView at run end: CRITICAL when `total >= cfg.mirror_failure_min_sample
   (10)` and `failed/total >= cfg.mirror_failure_ratio_threshold (0.5)`; else ERROR
   when any PERMANENT group; else nothing. Message text kept; `_failure_summary` takes
   a view.
3. **`health_logger` + routing untouched** — name `"dd.beacon.mirror.health"`;
   `dd/common/discord_logging.py` (alerts channel, 5s batch windows, per-signature
   dedup ×count, storm promotion ≥10/300s, owner-ping debounce 600s) is NOT modified.
4. **Disable-sweep escalation** — same thresholds/message shape, emitted from the
   worker's run-end hook.
5. **Per-target failures** stay local `logging.warning` (below the Discord alert
   threshold), as today.
6. **Cancel button** kept on SEND/UPDATE, absent on DELETE.

## 7. Config constants (`dd/common/cfg.py`, near the existing mirror block; baked-in, no env)

```python
mirror_claim_batch_size = 50
mirror_claim_stale_seconds = 15 * 60   # CLAIMED older than this is reclaimable
mirror_poll_interval = 45              # lazy poll backstop (s) when no nudge arrives
mirror_send_max_attempts = 3           # old send retry_threshold
mirror_edit_max_attempts = 2           # old update/delete retry_threshold
```

Reused unchanged: `mirror_max_concurrency`, `mirror_rate_per_sec`,
`mirror_retry_min/max`, `mirror_failure_ratio_threshold`, `mirror_failure_min_sample`,
`mirror_disable_forgiveness_hours`, `disable_bad_channels`.

## 8. Ordered execution steps (commit boundaries; conventional commits, scope `mirror`)

`make check` after every commit.

1. **Branch setup** (§2) + `chore(mirror): add delivery-ledger rewrite plan`
   (commits this file as `plans/mirror_v2_delivery_ledger.md`).
2. **`feat(mirror): add MirrorDelivery ledger schema and methods`** — `DeliveryState`,
   model, `_insert_ignore`, outcome dataclasses, methods: `enqueue_send`,
   `bump_for_edit`, `mark_deleted`, `cancel_pending`, `claim_batch` (+`ClaimedRow`),
   `flush_outcomes`, `prune`, `MirroredChannel.disable_failing_mirrors`, adapted
   `undo_auto_disable_for_failure`. Old model/columns still coexist (tests use
   `create_all`). New cfg constants. New tests: `test_mirror_delivery_schema.py`,
   `test_mirror_disable_query.py`.
3. **`feat(mirror): add convergence worker, flusher and run views`** —
   `dd/beacon/mirror_worker.py` complete; `RunView`/`RunFailure`/`Outcome` in
   `mirror_core.py` alongside the old tracker (nothing deleted yet — `mirror.py` still
   compiles). New tests: `test_mirror_worker.py`, `test_mirror_flusher.py`,
   `test_run_view.py`.
4. **`refactor(mirror): rewrite mirror extension onto the ledger`** — the big swap, one
   commit so the tree never half-works: listeners → enqueue+nudge; progress UI +
   `flag_mirror_failure_ratio` → RunView (drop first-pass line, add disabled-count);
   cancel → `cancel_pending`; `StartedEvent` starts the worker (incl. backlog
   recovery); `prune_message_db` → ledger; delete `_confirm_dead_dests`, the tracker/
   registry machinery, `MirroredMessage`, strike columns/methods. Rework
   `dd/beacon/extensions/testing.py::MirrorFailRateBump` (it called
   `add_confirmed_dead_strikes_in_batch`): insert synthetic FAILED/`confirmed_dead=1`
   ledger rows across `times` distinct fake `src_msg_id`s with `finished_at` older than
   the forgiveness window (preserves the owner's live-test workflow). Delete/adapt
   tests per §9.
5. **`feat(mirror): migrate to the mirror_delivery table`** — hand-authored migration
   (§3.2) + `atlas migrate hash`; verify `--print-ddl` parity.
6. **`docs(mirror): update architecture notes`** — fix the `mirror_core.py` line in
   `docs/architecture.md` and mention the worker/ledger.
7. Merge to `dev` when green; remove `plans/mirror_v2_delivery_ledger.md` in the merge
   or a final `chore(mirror)` commit. **No deploy without explicit owner confirmation.**

## 9. Test plan (`dd/beacon/tests/`, run via `make test`)

**Keep unchanged:** `test_classify_error.py`, `test_confirm_dest_unsendable.py`,
`test_rate_limiter.py`, `test_message_update_gate.py`, `test_crosspost_wait.py`,
`test_ignore_non_src_channels.py`, `test_format_duration.py`,
`test_server_statistics.py`, `test_mirror_tracing_cache.py`.

**Delete:** `test_tracker.py`, `test_run_till_completion.py`, `test_kernel_registry.py`,
`test_reconcile.py`, `test_send_edit_serialization.py` (superseded by version-guard
tests), `test_confirm_dead_dests.py` (probe moved into the worker).

**Adapt:** `test_schemas_mirrored_channel.py` (keep add/fetch-ordering/remove; drop
strike tests; add undo-resets-ledger), `test_render_mirror_progress.py` +
`test_mirror_progress_bar.py` (RunView input; first-pass line gone; throughput/ETA
strings unchanged; disabled-count line only when >0), `test_mirror_integration.py`
(`discord` marker, live — new impl signatures).

**New:**
- `test_mirror_delivery_schema.py` (`integration`): enqueue inserts only enabled+legacy
  dests minus source; idempotent re-enqueue on both dialects; bump_for_edit (version
  bump, FAILED/CANCELLED→PENDING, deleted untouched, missing-dest insert);
  mark_deleted CASE semantics; cancel_pending; claim ordering (population desc,
  unknown-first, created_at tiebreak); stale-claim reclaim; due_at gating;
  flush_outcomes every kind **including the version guard** (success after edit-bump →
  PENDING with dest_msg_id recorded; success after delete → PENDING; terminal failure
  after edit-bump → PENDING); prune retention (21d/90d).
- `test_mirror_disable_query.py` (`integration`): 3 confirmed-dead FAILED across
  distinct src msgs + streak >48h → disabled; 2 distinct → not; success resets streak;
  interleaved success/failure; per-pair granularity (second pair sharing the dest
  unaffected); recovery-via-edit resets; forgiveness window boundary (pin to
  `cfg.mirror_disable_forgiveness_hours`); undo re-enables + CANCELs FAILED rows so the
  next sweep is a no-op.
- `test_mirror_worker.py`: fake bot; op selection per row shape; one source fetch per
  group; transient→backoff outcome; attempts caps 3/2; PERMANENT → probe → confirmed_dead;
  probe exception → not confirmed, never raises; cancel_requested short-circuit;
  permanent source-fetch failure → group cancelled; backlog recovery registers views.
- `test_mirror_flusher.py`: outcomes accrued during a write flush next cycle (swap
  semantics); DB error re-queues and retries; no Discord objects touched.
- `test_run_view.py`: counts/breakdown/is_complete/supersede;
  `flag_mirror_failure_ratio` thresholds against a view.
- `test_mirror_claim_mysql.py` (`integration`, skip unless `TEST_USE_MYSQL`): two
  concurrent sessions claim disjoint rows (SKIP LOCKED). SQLite skips this file; the
  claim method itself degrades silently there (dialect drops FOR UPDATE), safe
  single-process.

## 10. Verification

1. `make check` green (the CI mirror: ruff → ty → pytest `-m "not discord"`).
2. `TEST_USE_MYSQL=1 make test-integration` against a **local** MySQL if available
   (conftest refuses non-local; never set `ALLOW_REMOTE_SCHEMA_DESTROY`).
3. `uv run --env-file .env python dd/common/schemas.py --print-ddl` — `mirror_delivery`
   present; `mirrored_message` + strike columns absent; DDL matches migration end-state.
4. `atlas migrate hash` clean (`atlas` is at `/usr/local/bin/atlas`); `atlas migrate
   validate --dir file://migrations`.
5. Grep gates — zero references remain to: `MirroredMessage`, `KernelWorkControl`,
   `kernel_work_control_registry`, `build_reconcile_targets`, `legacy_disable_strikes`,
   `legacy_failing_since`, `add_confirmed_dead_strikes_in_batch`,
   `clear_mirror_strikes_in_batch`.
6. Live smoke on dev (owner-confirmed deploy only): `/testing mirror create` + post →
   progress card with new lines; edit mid-send → old card "Superseded by edit", new run
   converges without duplicates; reworked fail-rate-bump ×3 → sweep + "Disabled
   channels: N"; delete → dest messages removed; kill the bot mid-fan-out → restart →
   recovery card appears and backlog converges within `mirror_claim_stale_seconds`.

## 11. Out of scope

- Non-legacy channel-follow mirrors and their surface (`autoposts.py`,
  `mirror_tracing.py`); `dd.anchor`; `dd/common/discord_logging.py`;
  `count_dests` consumers (`controller.py`, `statistics.py`) — API unchanged.
- `confirm_dest_unsendable`, `classify_error`, `RateLimiter` internals (survive
  verbatim).
- Multi-process horizontal scaling beyond claim safety (design supports it; one beacon
  process runs today).
- Dedup-probe before re-sending stale claims (owner accepted the duplicate window).
- Any prod deploy; any `atlas migrate diff` (needs Docker — hand-author + hash instead).

## 12. Accepted risks (owner sign-off obtained)

- **Crash duplicate-send window:** a crash between a Discord send and its flush re-sends
  those dests once after the stale-claim timeout. Strictly smaller than today's
  whole-run loss.
- **FAILED retention 90 days** vs today's forever-strikes: a dest failing on a source
  quieter than ~monthly may age out evidence before reaching 3 strikes.
- **Edit backoff interaction:** a transient-failing dest retries on its backoff
  schedule even if an edit arrives meanwhile (deliberate, §5.5).
