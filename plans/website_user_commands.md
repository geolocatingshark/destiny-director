# Website interface for user-defined commands

## Status: BLOCKED on `plans/website_discord_previews.md`

## Goal

Add a web interface for managing user-defined `/command`s (`dd/beacon/extensions/
user_commands.py`), replacing/complementing the old `/command` control surface. Include
a live preview of what the command's response will look like when posted, the same way
the weekly_reset/trials web forms preview their post.

## Why blocked

There's no reusable previewer yet — `renderPreview`/`#previewBox` is currently
embedded in the weekly_reset form JS/CSS. Building the user-commands preview against
that would just add a third copy. Wait until `website_discord_previews.md` lands a
standalone previewer, then consume it here.

## Not yet scoped

- Auth/permissions model for who can create/edit/delete user commands via the web UI
  (currently gated in Discord via `dd.common.auth`).
- Whether user commands need the same create/edit/publish lifecycle as weekly_reset/
  trials (`hybrid_post_core`'s contract — see `plans/trials_web_form_sharing.md`) or a
  simpler CRUD shape.
- Relationship to the self-heal behavior noted in `plans/legacy_rotation_cleanups.md`
  item 6 (DB-backed command row deleted on name clash with a code-defined command) —
  a web UI surfacing that clash live could double as its recovery path.
