# Website interface for user-defined commands

## Status: partially unblocked — reusable previewer exists; embed/message-content render is THIS plan's job

## Goal

Add a web interface for managing user-defined `/command`s (`dd/beacon/extensions/
user_commands.py`), replacing/complementing the old `/command` control surface. Include
a live preview of what the command's response will look like when posted, the same way
the weekly_reset/trials web forms preview their post.

## Previewer status (from `plans/website_discord_previews.md`)

The **reusable previewer is done**: client `initPostPreview` + server `render_post_spec(PostSpec)`
(the shared render path). Consume those here — do NOT add a fourth copy.

> **THIS PLAN OWNS the embed + message-content preview render.** The shared previewer renders
> **CV2 posts only** today (`PostSpec` kind `"cv2"`). user-commands is the first consumer that
> needs to preview **classic embeds** (`response_type 3` → `h.Embed`) and **plain message
> content** (and copied-message/CV2 responses). So Part C of `website_discord_previews.md` —
> adding the `{kind:"embed", …}` variant to `PostSpec` and an embed→safe-HTML branch in
> `render_post_spec` (mirroring `dd.common.components.embeds_to_container`'s mapping), plus a
> plain-content branch — lands **as part of building this plan**, together with the
> client-authored spec-POST endpoint (`POST /post/preview`) that this UI drives. Budget for it
> here; it is not delivered by the previews plan on its own.

## Not yet scoped

- Auth/permissions model for who can create/edit/delete user commands via the web UI
  (currently gated in Discord via `dd.common.auth`).
- Whether user commands need the same create/edit/publish lifecycle as weekly_reset/
  trials (`hybrid_post_core`'s contract — see `plans/trials_web_form_sharing.md`) or a
  simpler CRUD shape.
- Relationship to the self-heal behavior noted in `plans/legacy_rotation_cleanups.md`
  item 6 (DB-backed command row deleted on name clash with a code-defined command) —
  a web UI surfacing that clash live could double as its recovery path.
