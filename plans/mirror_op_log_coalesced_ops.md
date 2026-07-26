# Operation log — coalesced create+edit records one mislabeled op (LOW PRIORITY)

**Status:** deferred / filed. From the 2026-07-26 code review.

## Problem

The mirror drain watcher **coalesces** all events for a source into one watcher
(`start_drain_watcher` is a no-op while a watcher is live). So if a message is **edited
before its create fan-out finishes draining**, there is a single watcher whose `view.op`
is the original `SEND`/create. When it drains, `_record_operation` (in
`dd/beacon/extensions/mirror.py`) writes **one** `mirror_operation_log` row:

- `op_type = "create"` (from the original view), and
- `version = MirrorDelivery.op_meta(...)` = the **final** `desired_version` (e.g. 2).

Consequences on the web log:

- the row is tagged `create` but `version=2`, so the **v2 column is labelled CREATE**;
- the **update operation is never recorded** at all;
- the **v1 column shows "counts not recorded"** (no op row has version 1).

This only happens when the edit lands *during* the initial fan-out (≈150 channels,
rate-limited — seconds to minutes). An edit after delivery completes drains separately
and records correctly (create v1 + update v2), which is the common case.

## Why it's not urgent

- Common path (edit after delivery) is correct.
- No crash, no delivery impact — purely the observability log's labelling.
- Mirrors the retired progress card's "last drain wins" coalescing, so it's a
  pre-existing modelling choice, not a new regression.

## Options to fix later

1. **Record per materialized version, not per drain.** Snapshot capture already fires
   once per `(src_msg_id, version)` in `MirrorWorker._source_for`; have *it* (or a
   sibling) write/upsert an operation row per version with `op_type` inferred (v1 =
   create, later = update), then fill in the final counts when that version's fan-out
   drains. Decouples op rows from the coalesced watcher. Bigger change.
2. **Carry the op sequence on the view.** Track the ops that coalesced into one watcher
   (e.g. a list of `(op_type, version)`), and have `_record_operation` write one row per
   distinct version with the right `op_type`, splitting the final counts (or marking the
   intermediate ones "counts folded into vN"). Medium change; counts still can't be
   cleanly split per op (the ledger only has the final tally).
3. **Minimal honesty fix.** If `op.op_type == "create"` but `version > 1`, render the
   column label as "Create+Edits" (or tag it "create→vN"), and stop showing the earlier
   version columns as "counts not recorded" when a later same-message op absorbed them.
   Cheapest; just makes the UI honest about the coalescing without new storage.

## Where

- `dd/beacon/extensions/mirror.py` — `_record_operation`, `start_drain_watcher`,
  `_run_drain_watcher`, `_OP_TYPE`.
- `dd/common/schemas.py` — `MirrorDelivery.op_meta`, `MirrorOperationLog`.
- `dd/anchor/web_static/mirror_log.js` — `renderVersionColumns` (`opByVersion` matching,
  the "counts not recorded" fallback).
