# Move `ls_update` to the web, via the weekly-reset lifecycle mechanism — stub

## Status: deferred note (2026-08-04). Mechanism chosen, not scoped.

Spun out of `plans/anchor_command_web_migration.md`, which keeps `ls_update` as a Discord
context menu in its minimal set. This records the intent to migrate it later, and **which
existing mechanism it should reuse**, so neither is re-litigated.

## What it is

`dd/anchor/extensions/lost_sector.py:73` — a message context-menu command, registered in
the Kyber guild in addition to control + test_env. Right-click a lost-sector announcement
▸ Apps ▸ `ls_update`: it rebuilds today's post via `format_post` and edits the target
message in place. Refuses if lost-sector autoposts are disabled.

## Why it survived the first cut

It is anchored to a specific Discord message, and it is distinct from `Edit post` —
`Edit post` opens a builder on the message's *current content*, whereas `ls_update`
discards that content and re-renders from *live data*. Nothing else in the kept set
covers it.

## The mechanism: copy weekly reset's post lifecycle

Owner's call — and it is the right shape, because weekly reset already solved exactly the
"edit the post I published earlier, from the web" problem.

The relevant machinery is `dd/anchor/hybrid_post_core.py`:

- **`DraftMeta`** (`hybrid_post_core.py:521`) persists `message_id`, `status`
  (`draft` / `posted` / `published`), `crossposted` and `reset_ts` for the period's post.
- **`post_or_edit_unpublished`** (`hybrid_post_core.py:953`) is the key call: when a
  message id is already on record it **edits that message in place** instead of sending a
  fresh one.
- Weekly reset wires these up through a `HybridPostSpec` and thin wrappers
  (`weekly_reset.py:831`, `weekly_reset.py:838`), and drives them entirely from web
  routes (`/weekly_reset/edit`, `/preview`, `/create`, `/delete`).

`ls_update` is the same operation with the content generated instead of hand-authored. So
the migration is: **give the cron-posted feeds a `DraftMeta`-equivalent record of the
message they last posted**, and "re-render latest" becomes a button that calls the feed's
`message_constructor_coro` and edits that message — no right-click, no target to pass.

## The one genuinely missing piece

Weekly reset knows its message id because the *web form* created the post. Lost sector's
post is created by the **cron announcer**, which does not record what it sent:
`discord_announcer` (`dd/anchor/autopost.py:32`) calls `utils.send_message` and discards
the result. That is the gap to close first — persist the sent message id per feed per
period, keyed by followable name (the shared key established in
`plans/anchor_web_ia.md` §1).

Check `mirror_delivery` before adding storage: the mirror log already resolves runs by
`src_msg_id` + `followable_name`, so the id may already be recoverable without a new
table. Verify what happens when the post was sent but the mirror run failed — if the
ledger row is the only record, that failure mode loses the id.

## Open questions before scoping

- Should this generalise? Every CV2 feed has a `message_constructor_coro`, so "re-render
  the latest post for feed X" fits all six, not just lost sector. That argues for building
  it once on the Phase 1 feed panel rather than porting one command.
- `ls_update` refuses when lost-sector autoposts are disabled. Keep that guard, or is
  re-rendering a past post reasonable while the feed is off?
- Crossposted messages: `post_or_edit_unpublished`'s docstring notes an edit to a
  crossposted message behaves differently from an unpublished one. Lost-sector posts are
  crossposted by default (`publish_message=True`), so confirm the edit still propagates.
- The Kyber-guild registration means someone other than the panel's usual audience may be
  using it. Confirm before removing the Discord entry.

## Dependency

The feed panel from Phase 1 of `plans/anchor_command_web_migration.md`. This is a natural
follow-up to that page existing, not standalone work.
