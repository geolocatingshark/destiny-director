# Embeds and CV2 as parallel, first-class citizens

## Goal

Stop treating classic-embed posts as the legacy path that CV2 is migrating away from
(see `plans/convert_command_fold_into_edit.md`, which is explicitly gated on an
embed→CV2 *migration*). Instead, make **Edit** and **Copy** in
`dd/anchor/extensions/posts.py` dispatch to the right renderer/editor for whichever
format the target message is actually in — embed or CV2 — as two supported formats,
not a source and a destination.

Today: `EditComponents` (`posts.py:237`) hard-rejects non-CV2 via
`_reject_unless_own_cv2` (`posts.py:149`); `CopyEmbed` (`posts.py:98`) and
`CopyComponents` (`posts.py:275`) are separate commands split along the same line.

## Why

`convert_command_fold_into_edit.md` frames embed support as a one-way bridge into CV2
("irreversible conversion", gated until CV2 migration is done). This plan instead asks:
what if both stay first class indefinitely? Reconcile the two plans before executing
either — they may conflict (fold-into-edit assumes embeds are being phased out; this
one assumes they're not).

## Scope

- **Edit**: choose the embed editor or the CV2 editor based on the target message's
  actual format, instead of erroring on the wrong one.
- **Copy**: same dispatch — `CopyEmbed`/`CopyComponents` could collapse into one command
  that picks the right copy path, mirroring the Edit change.

## Not yet scoped

- Resolve the conflict with `plans/convert_command_fold_into_edit.md` (parallel-forever
  vs. bridge-then-retire) before implementation.
- Whether "Copy" collapsing into one command is a straight merge or needs the same
  irreversible-action care as Edit's embed→CV2 path.
- Interaction with `plans/website_discord_previews.md` (a previewer that has to render
  both formats supports this framing).
