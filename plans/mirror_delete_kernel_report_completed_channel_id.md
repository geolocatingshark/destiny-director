# Mirror delete kernel: `report_completed` keyed on `dest_msg.channel_id` vs `ch_id`

> **Deferred — revisit during the mirror system refactor** (while watching the mirror
> system for misbehaviours). Do not fix in isolation; see the caveat below.

## Finding

In `dd/beacon/extensions/mirror.py`, the **delete** kernel reports completion using the
fetched message's channel id rather than the loop's `ch_id`:

```python
# mirror.py ~line 1038 (delete kernel)
else:
    tracker.report_completed(dest_msg.channel_id, msg_id)
```

Its siblings in the same kernel use `ch_id` consistently:
- `tracker.report_scheduled(ch_id, msg_id)` (~1021)
- `tracker.report_failure(ch_id)` (~1036)

And the **create** kernel (~785) and **update** kernel (~960) both call
`report_completed(ch_id, ...)`. So the delete kernel is the odd one out.

`KernelWorkControl.report_completed` → `_report_try` does `self._tries[channel_id] += 1`
and `self._scheduled.pop(channel_id)`, both populated under `ch_id` via `report_scheduled`.
So passing `dest_msg.channel_id` only matches when `dest_msg.channel_id == ch_id` (the
normal case). In a thread/parent edge case where they differ, this would `KeyError` /
mis-record the completion and cause needless delete retries.

## Why NOT to "just fix" it now

The obvious one-liner (`dest_msg.channel_id` → `ch_id`) was **intentionally deferred**.
Reason (per gsfernandes): there may be a condition under which **one of `ch_id` /
`dest_msg.channel_id` is not defined / not the value you'd expect**, so naively swapping
could trade one edge-case bug for another. This needs to be investigated *with the mirror
system under observation*, not as a blind consistency edit.

## When revisiting

- Determine under what conditions `dest_msg.channel_id` can diverge from `ch_id` (threads,
  forum/parent channels, cross-posted/mirrored sources).
- Confirm which of the two is the correct progress-tracking key for delete, and whether the
  create/update kernels' use of `ch_id` is actually right in those same conditions.
- Align all three kernels once the correct invariant is established.

Related: this surfaced during the v3 manual-testing pass while auditing message-handling
listeners for the `channel_id`-vs-`message.id` bug class (the actual bug found & fixed was in
`free_games.py`). This mirror item is a *different* class (channel-vs-channel) and only a
latent robustness issue, not a confirmed user-facing break.
