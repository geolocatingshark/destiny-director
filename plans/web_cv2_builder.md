# Web Components-V2 builder — reusable CV2 authoring + safe-HTML preview

## Status: ready to build (spun out of `plans/website_user_commands.md`, 2026-07-19)

## Context — why this exists

Components-V2 (CV2) is Discord's modern rich-message format (containers, text displays,
sections with accessories, media galleries, separators, buttons, per-container accent). We want
a **reusable web builder** to author CV2 posts visually with a live, XSS-safe HTML preview.
First consumer is the web command manager (`plans/website_user_commands.md`) via a **new
`response_type 4` = Components-V2**; it is designed as a standalone module so other post
surfaces can adopt it later.

**Headline:** the effort is small because the JSON-first CV2 model already exists in the repo.
The in-Discord `/post components` command (`dd/anchor/extensions/posts.py`) already builds CV2
posts over `dd/anchor/cv2_nodes.py`. This plan is largely a **web front-end over that model**
plus **one new backend piece** (a node-tree → HTML preview renderer).

## Owner decisions (2026-07-19)

- **New CV2 response type** (`4`) for user commands, stored as node JSON.
- **Reusable shared module** — its own JS widget + server serialize/validate/preview contract.
- **Full node tree in v1** — containers, text displays, sections + accessories, media
  galleries, separators, link buttons, accent color (mirrors the in-Discord builder's reach).

## Reuse (verbatim, no reinvention) — verified against code

- **`dd/anchor/cv2_nodes.py`** — the entire node model, I/O-free: `Node = dict` (a raw Discord
  component payload), typed constructors (`make_container` / `make_text` / `make_section` /
  `make_media_gallery` / `make_separator` / `make_thumbnail` / `make_button` /
  `make_link_button`), per-kind field specs + `mutate_*`, tree ops (`resolve_path`,
  `scope_children`, `insert_node`, `delete_node`, `move_node`, `set_accessory`), nesting rules
  (`addable_kinds`, `ADD_LABELS`, depth ≤ 3), `sanitize_for_preview` (downgrades invalid /
  mid-construction nodes to placeholders so a preview never errors), and `validate` (returns
  human-readable send-blocking problems). **Model, validation, nesting are all done.**
- **`dd/anchor/cv2_raw.py`** — `RawComponentBuilder` (sends a raw node list; hikari auto-sets
  `IS_COMPONENTS_V2` from each node's `type`) and `fetch_raw_message_components(channel_id,
  message_id)` (REST load of an existing post's nodes for editing). **Send + load are done.**
- **`dd/anchor/cv2_builder.py`** + `/post components` — the in-Discord add/edit/delete/move/
  open/back/done state machine over the node model; the **UX reference** to mirror in HTML.

## The one genuinely new backend piece

`render_cv2_nodes_html(nodes, emoji_dict) -> str` — a node-tree → XSS-safe HTML renderer. (The
in-Discord builder "previews" by actually **sending** sanitized `RawComponentBuilder` dicts to
Discord; the web needs an HTML render instead.) It runs `cv2_nodes.sanitize_for_preview(nodes)`
first, then renders each node kind through the **same whitelist** the flat previewer already
uses (`render_post_html`'s `_render_line`/`_render_inline`/`_html_emoji_substituter`,
`{strong,em,span,a,img}`, escaped leaves, http(s)-validated URLs):
- **container** → `<div class="post-embed" style="--accent:#RRGGBB">…</div>` (validated hex),
  children rendered in order;
- **text display** → the inline-markdown machine;
- **section** → text displays + one accessory (thumbnail `<img>` or a link/interactive button
  chip on the side);
- **media gallery** → one `<img class="post-image">` per item;
- **separator** → a rule/spacer honoring `divider`/`spacing`;
- **action row** → `.post-buttons` link buttons (interactive buttons render as inert chips).

## Design

- **Reusable web widget:** `dd/anchor/web_static/cv2_builder.js` (+ `cv2_builder.css`) — a tree
  editor over the node model mirroring `cv2_builder.py`'s state machine (add via `addable_kinds`,
  edit fields via the per-kind field specs, delete/move/open/back, set accessory). Emits the node
  list JSON. Framework-free, mounted by a host page.
- **Preview seam:** expose `render_cv2_nodes_html` as its **own entry point** the host calls
  directly (recommended — keeps `PostSpec` flat/hashable rather than adding a `nodes`-carrying
  kind). The host's `/preview` route runs `sanitize_for_preview` → `render_cv2_nodes_html` and
  returns the HTML; `/validate` surfaces `cv2_nodes.validate` problems. Client injects the HTML
  via the same trusted-`innerHTML` contract as `initPostPreview`.
- **Storage/send:** persist the node list as JSON in the consumer's field; send live via
  `RawComponentBuilder`. **No new CV2 persistence layer.** Editing an existing live post can
  seed from `fetch_raw_message_components`.

## Files

- **Create:** `dd/anchor/web_static/cv2_builder.js` (+ `.css`); `render_cv2_nodes_html` (in
  `dd/anchor/hybrid_post_core.py` next to `render_post_html`, or a small `dd/anchor/cv2_html.py`);
  `dd/anchor/tests/test_cv2_html.py`.
- **Reuse as-is (no changes expected):** `dd/anchor/cv2_nodes.py`, `dd/anchor/cv2_raw.py`,
  `dd/anchor/cv2_builder.py`.

## Verification

`make check`. Unit-render each node kind + the `sanitize_for_preview` path + XSS safety (assert
whitelist-only tags, hex-validated accent, escaped leaves). Manually build a multi-node post in
the web widget and confirm the HTML preview matches an actual Discord send of the same nodes.
`cv2_nodes`' existing validation tests still cover the model (unchanged).

## Consumers / dependents

- `plans/website_user_commands.md` — `response_type 4` mounts the CV2 builder widget, previews
  via `render_cv2_nodes_html`, and the beacon type-4 runtime sends stored node JSON via
  `RawComponentBuilder`.

## Risks

- **XSS:** node text/URLs flow user input into HTML — everything through the shared whitelist +
  `html.escape` + http(s) validation; accent validated to hex.
- **Preview vs. live drift:** always `sanitize_for_preview` before rendering, and render from the
  same node list that gets sent, so the HTML preview tracks the real post.
- **Interactive buttons** have no live behavior in a static preview — render them as inert chips
  and rely on `cv2_nodes.validate` for send-blocking rules.
