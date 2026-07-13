# Add a "back to homepage" link to every anchor web page

## Goal

The anchor web UI has a landing page — the **Control Panel** at `/`
(`dd/anchor/extensions/control_panel.py`) — whose cards link *outward* to each tool.
Nothing links back. Once you follow a card into the rotation editor, autopost settings
or the weekly-reset form, the only way back to the panel is the browser back button.

Add a consistent, unobtrusive "← Control panel" link to the top of every full HTML
page so the homepage is always one click away.

## Background (as-is)

- Homepage: `GET /` → `control_panel.html` (card grid).
- There is **no Jinja / no shared layout partial**. Each page is a static file in
  `dd/anchor/web_static/` whose handler `str.replace()`s a placeholder. So a nav link
  must be added to each file individually.
- Every full page links `/static/shared.css` (reset/theme only — `body`, `header`,
  `h1`, `.muted`; no nav markup).
- The **only** existing back-link today is on the rotation edit form,
  `editor.html`: `<a href="/rotation" class="backlink">← All rotations</a>`. Its
  `.backlink` style lives in **`editor.css`**, so no other page can reuse it.

Full HTML pages (the ones users land on):

| Page | Route | File |
|------|-------|------|
| Control Panel (homepage) | `/` | `control_panel.html` |
| Rotation Editor home | `/rotation` | `rotation_home.html` |
| Rotation edit form | `/rotation/edit` | `editor.html` |
| Autopost Settings | `/autopost_settings` | `autopost_settings.html` |
| Weekly Reset Overview | `/weekly_reset` | `weekly_reset_form.html` |

The auth redirect handlers (`/auth/*`) and OAuth callbacks return redirects / plain
text, not HTML pages — out of scope.

## Plan

1. **Promote `.backlink` to `shared.css`** so every page can use it (all pages already
   link `shared.css`). Remove the now-duplicate rule from `editor.css`.

2. **Add a `← Control panel` link** as the first element inside `<header>` on each
   non-homepage full page: `rotation_home.html`, `autopost_settings.html`,
   `weekly_reset_form.html`.

3. **`editor.html`** already has `← All rotations`. Keep it but add `← Control panel`
   alongside it (editor is two levels deep: `/` → `/rotation` → `/rotation/edit`), so
   both parents are reachable.

4. **`control_panel.html`** is the homepage itself — no self-link needed.

## Out of scope

- No new shared-header partial / templating engine — not worth it for one link across
  five static files; keep the project's existing "static file + placeholder" style.

## Verification

- `make lint` / `make test` stay green (these are static assets; the control-panel
  tests in `dd/anchor/tests/test_control_panel.py` still pass).
- Manual: load each page, confirm the link renders and points to `/`.
