# Website UI — move to the Trials set-card palette (design tokens)

## Status: STUB — not yet scoped for implementation

## Goal
Pull the whole anchor web UI in the direction of the Trials **set-card** look
(`dd/anchor/web_static/trials_form.css`, the `.set-*` rules): layered dark surfaces, an
**accent-driven** selected/hover/focus system, and consistent text tiers — so the rotation
editor, the weekly-reset form, the Trials form, and the control panel read as one design
system instead of four independently hand-tuned stylesheets.

## Why now
The colours are good but **hardcoded and duplicated**. `shared.css` defines only a page
background + `.muted`/`.backlink`; every page CSS then re-hardcodes the same greys/blues:
- surfaces `#1b1d22` (page) / `#202329` (fieldset) / `#24272e` (control, card) /
  `#14161a`–`#1f2229` (inset/dropdown),
- borders `#3a3f47`, hover `#4a5670`,
- accent `#2e5bff` (and the CV2 embed colour already piped in as `--accent`), selected
  tint `#1c2333`, accent text `#9fb4ff` / `#cfe0ff`,
- text tiers `#e6e6e6` / `#cfd3d6` / `#b9bec4` / `#8b9096` / `#9aa0a6`,
- feedback ok `#7ee787`, err `#ff7b72`, danger surface `#3a1e1e`.

The set-cards already lean on `--accent` (the per-post CV2 colour from the bootstrap) for
their selected/focus states — that accent-driven pattern is the thing worth generalising.

## Rough direction (to be refined)
1. **Define tokens once** in `shared.css` `:root` — e.g. `--surface-0/1/2/3`, `--border`,
   `--border-hover`, `--accent` (default + overridable per page), `--accent-tint-bg`,
   `--text-1/2/3`, `--ok`, `--err`, `--radius`. Seed values from the list above.
2. **Migrate page CSS to the tokens** — `editor.css`, `weekly_reset_form.css`,
   `trials_form.css` — replacing literal hex with `var(--…)`. Behaviour-preserving; mostly
   a find-and-replace + reconciling the few near-duplicate greys onto one scale.
3. **Adopt the card interaction pattern** where it fits — accent border/tint on
   selected/active, subtle border on hover, `:focus-visible` ring in `--accent` — for the
   editor's set-pool rows, list controls, and buttons, so selection feels the same
   everywhere.
4. Optionally let each page keep piping its CV2 accent into `--accent` (Trials/weekly-reset
   already do) so interactive accents match the post's embed colour.

## Files
- `dd/anchor/web_static/shared.css` (add tokens)
- `dd/anchor/web_static/{editor,weekly_reset_form,trials_form}.css` (consume tokens)
- Reference implementation: the `.set-*` rules in `trials_form.css`.

## Not yet scoped
- Whether to introduce a light theme (today `color-scheme: dark` only) — tokens would make
  it possible but it's out of scope unless wanted.
- Exact surface scale (how many levels; collapsing `#1f2229`/`#202329`/`#24272e`).
- Accessibility pass: contrast-check the text tiers against each surface (esp. `--text-3`
  on `--surface-2`) once the scale is fixed.
- Touching the CV2 image generation palette (`dd/anchor/cv2_*`) — this stub is web CSS
  only; the posts' rendered images are a separate surface.
- Coordinating with any shared-previewer / form-sharing work
  (`plans/trials_web_form_sharing.md`, `plans/website_discord_previews.md`) so the token
  file lands before those add more page CSS.
