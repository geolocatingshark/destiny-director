# Website Discord previews — reusable previewer

## Goal

Extract the message preview used by the weekly_reset web form (`renderPreview` /
`#previewBox` in `dd/anchor/web_static/weekly_reset_form.js` + `.css`, fed by
`hybrid_post_core`'s safe-HTML preview endpoint) into its own standalone, reusable
piece of client code — not tied to weekly_reset specifically. `trials_form.js` already
duplicates most of this (see `plans/trials_web_form_sharing.md` section B, which covers
sharing the surrounding form lifecycle JS more broadly).

Then use the extracted previewer to standardise **all** rotation previews (legacy
activities, lost sector, xur, etc. — whichever have or will get a web form/editor) so
each shows a representative sample of what the Discord post will actually look like.

## Why

This is a prerequisite for `plans/website_user_commands.md` (user-defined `/command`
management needs a preview too, and should reuse the same previewer rather than growing
a fourth copy).

## Scope note — embeds AND CV2

Today's preview path renders safe HTML from server-side markdown-ish text
(`hybrid_post_core.preview`), which maps naturally to CV2/Components-V2-style posts.
Full support needs to also preview classic embed-based posts (see
`plans/embeds_and_cv2_parallel_first_class.md`) so the same previewer works regardless
of which renderer a given command/rotation uses.

## Not yet scoped

- Which rotations/commands get a preview first.
- Whether the previewer is a shared JS module (`shared.js`) or a small web component.
- Server-side: does every producer need its own `/preview` route, or can this become a
  generic endpoint given a post spec?
