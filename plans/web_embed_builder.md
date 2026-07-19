# Web embed builder — reusable classic-embed authoring + safe-HTML preview

## Status: ready to build (spun out of `plans/website_user_commands.md`, 2026-07-19)

## Context — why this exists

Classic Discord embeds (`h.Embed`, user-command `response_type 3`) are authored today only by
hand-writing embed-kwargs JSON in the in-Discord `/command` UI — blind, with no preview, which
is why `EMBEDS_FEATURE_FLAG` keeps the option hidden. We need a **reusable web builder** that
lets an owner assemble an embed with structured fields and see a live, XSS-safe HTML preview
that matches what Discord will render. First consumer is the web command manager
(`plans/website_user_commands.md`, `response_type 3`); it is designed as a standalone module so
other post surfaces can adopt it later. This plan also absorbs the embed-render half of the
old "Part C" from the deleted `plans/website_discord_previews.md`.

## Owner decisions (2026-07-19)

- **Structured field editor** (not a raw JSON textarea).
- **Reusable shared module** — its own JS widget + server serialize/validate/preview contract.

## Architecture facts (verified against code)

- The preview pipeline is `dd/anchor/hybrid_post_core.py`: `PostSpec` (frozen, hashable —
  `kind`/`body`/`image_url`/`buttons`), `render_post_spec(spec, emoji_dict)` dispatches on
  `kind` (`cv2` → `render_post_html`; anything else raises), and `render_post_html` is the
  XSS-safe markdown→HTML machine (whitelist `{strong,em,span,a,img}`, escaped leaves,
  http(s)-validated URLs, `:emoji:` via `_html_emoji_substituter`, `<t:…>` via `_format_ts`,
  block prefixes via `_render_line`, inline via `_render_inline`). `PostSpec.from_payload`
  currently raises `ValueError` on any non-`cv2` kind.
- `dd/common/components.py::_add_embed_to_container` (≈ lines 547-604) is the canonical
  embed→Components-V2 mapping (author→`-# [name](url)`, title→`## [title](url)`, description
  verbatim, thumbnail→section accessory / standalone, fields→`**name**\nvalue` separated,
  image→full-width media, footer+timestamp→`-#` subtext, color→container accent). The embed
  preview HTML must mirror this field-by-field so preview == live post.
- **The beacon type-3 runtime is thin** (`dd/beacon/extensions/user_commands.py:201`): it does
  `json.loads` → `color` default → pop `image` → `h.Embed(**kwargs)` → `set_image`. It applies
  **only `h.Embed` constructor kwargs** (title/description/url/color/timestamp) + image — NOT
  author/footer/fields/thumbnail. A structured editor is meaningless unless the runtime is
  extended to apply the full embed (below).
- `preview_emoji_dict(bot)` (hybrid_post_core, ≈ 679) — 5-min TTL emoji dict for preview routes.

## Design

### 1. One shared (de)serializer — kills drift

`embed_from_stored_json(data: str) -> h.Embed` in `dd/common/` (natural home:
`components.py`, next to `_add_embed_to_container`, or a small `embeds.py`). Canonical JSON
schema — a superset of today's rows (all keys optional, so existing type-3 rows stay valid):

```json
{
  "title": "…", "url": "…", "description": "…",
  "color": "#RRGGBB" | int, "timestamp": "ISO-8601",
  "image": "https://…", "thumbnail": "https://…",
  "author": {"name": "…", "url": "…", "icon": "https://…"},
  "footer": {"text": "…", "icon": "https://…"},
  "fields": [{"name": "…", "value": "…", "inline": true}]
}
```

Builds a fully-populated `h.Embed` (color default `cfg.embed_default_color`; applies author /
footer / fields / thumbnail / image via the `h.Embed` setters). Malformed JSON / bad color →
`FriendlyValueError` (so routes 400, not 500). This ONE function is consumed by (a) the beacon
type-3 runtime, (b) the preview renderer, and (c) is the inverse of what the editor emits.

### 2. `PostSpec` embed kind + `render_embed_html`

In `dd/anchor/hybrid_post_core.py`:
- Add field `embed: str | None = None` (the embed **JSON string** — keeps the frozen dataclass
  hashable; a dict would break `render_post_wall` keying), and a `PostSpec.embed_spec(
  response_data: str, image_url: str | None = None)` factory.
- Teach `from_payload` to accept `kind == "embed"` (read `payload["embed"]`); keep raising
  `ValueError` for other unknown kinds (preserves the 422 path).
- Add `elif spec.kind == "embed":` to `render_post_spec` → new `render_embed_html(response_data,
  emoji_dict)`: `embed_from_stored_json` → mirror `_add_embed_to_container` field-by-field,
  emitting **markdown strings** pushed through the existing `_render_line`/`_render_inline`
  whitelist. Color → a **validated hex** on a `<div class="post-embed" style="--accent:#RRGGBB">`
  wrapper (never interpolate raw). Timestamp via `_format_ts`.
- **Fidelity note:** the live handler does a `follow_link_single_step` redirect hop on the image;
  the preview render stays **sync** and skips it (documents a minor pre-redirect-URL
  discrepancy). Do not make the render path async.

### 3. Extend the beacon type-3 runtime

Rewrite the type-3 branch in `dd/beacon/extensions/user_commands.py:201` to build via
`embed_from_stored_json(cmd.response_data)`, so author/footer/fields/thumbnail render in the
live post. Keep the existing image redirect-hop behavior.

### 4. Reusable web widget

`dd/anchor/web_static/embed_builder.js` (+ `embed_builder.css`) exposing a framework-free global
`initEmbedBuilder({ mount, value, onChange })` that:
- renders the structured fields (title / url / description / color picker / image / thumbnail /
  author{name,url} / footer{text} / timestamp + a repeatable fields list {name,value,inline}),
- seeds from an existing embed JSON on edit (the inverse shape of `embed_from_stored_json`),
- emits the canonical JSON via `onChange` for the host page's `readForm()` + preview.

The host mounts this and drives preview through the existing `initPostPreview` contract (POST
the `{kind:"embed", embed:<json>}` spec to the host's `/preview` route, inject returned safe
HTML). CSS reuses `#previewBox`/`.post-preview` from `shared.css`; add `.post-embed` accent-bar
styling.

## Files

- **Create:** `dd/anchor/web_static/embed_builder.js` (+ `.css`); `embed_from_stored_json` (in
  `dd/common/components.py` or new `dd/common/embeds.py`).
- **Modify:** `dd/anchor/hybrid_post_core.py` (PostSpec embed kind, `render_embed_html`),
  `dd/anchor/tests/test_hybrid_post_core.py` (flip the two guard tests
  `…rejects_unknown_kind` / `…embed_kind_not_yet_supported`; add mapping tests),
  `dd/beacon/extensions/user_commands.py` (type-3 via the shared builder),
  `dd/common/tests/` (unit-test `embed_from_stored_json` round-trip / defaults / malformed).

## Verification

`make check`. Unit-render each embed part (author/title/description/fields/image/thumbnail/
footer/timestamp/color-accent) and assert whitelist-safe HTML (no raw `<script>`, hex-validated
accent). Confirm a stored row renders identically in the live post and the preview. Malformed
JSON surfaces a clean 400-able error, not a 500.

## Consumers / dependents

- `plans/website_user_commands.md` — `response_type 3` mounts `initEmbedBuilder` and previews
  via `render_embed_html`.

## Risks

- **XSS:** all embed text flows user input into HTML — everything through the
  `_render_line`/`_render_inline` whitelist + `html.escape` + http(s) validation; the accent
  value validated to a hex string, never raw into `style`.
- **Hashability:** carry the embed as a JSON string, not a dict.
