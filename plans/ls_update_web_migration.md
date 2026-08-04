# Move `ls_update` to the web — stub

## Status: deferred note (2026-08-04). Not scoped.

Spun out of `plans/anchor_command_web_migration.md`, which keeps `ls_update` as a Discord
context menu in its minimal set. This records the intent to migrate it later, so the
decision isn't re-litigated as "already settled".

**What it is.** `dd/anchor/extensions/lost_sector.py:73` — a message context-menu command,
registered in the Kyber guild in addition to control + test_env. Right-click a lost-sector
announcement ▸ Apps ▸ `ls_update`: it rebuilds today's post via `format_post` and edits the
target message in place. Refuses if lost-sector autoposts are disabled.

**Why it survived the first cut.** It is anchored to a specific Discord message, and it is
distinct from `Edit post` — `Edit post` opens a builder on the message's current content,
whereas `ls_update` discards that content and re-renders from live data. So it is not
covered by anything else in the kept set.

**Why it should still move.** The message it targets is not arbitrary: it is the most
recent lost-sector autopost, which the `mirror_delivery` ledger already knows about (the
mirror log page resolves runs by `src_msg_id` + `followable_name`). If the web side can
identify that message, the command's only Discord-specific input disappears and it becomes
a "re-render latest" button on the lost-sector feed panel.

**Open questions before scoping:**

- Can the target be resolved reliably from the ledger, or does it need a stored
  "last lost-sector post" pointer? Check what happens when the post was sent but the
  mirror run failed.
- Should it generalise? Every CV2 feed has the same `message_constructor_coro`, so
  "re-render the latest post for feed X" is a shape that fits all of them, not just lost
  sector. That argues for building it once on the feed panel rather than porting one
  command.
- The Kyber-guild registration means someone other than the panel's usual audience may be
  using it. Confirm before removing the Discord entry.

**Dependency:** the feed panel from Phase 1 of `plans/anchor_command_web_migration.md`.
This is a natural follow-up to that page existing, not standalone work.
