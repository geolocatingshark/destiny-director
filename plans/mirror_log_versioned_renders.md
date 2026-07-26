# Mirror-log versioned renders (Phases C–E) — deferred design

Extends the web mirror-log page (`/mirror-logs`, `dd.anchor.extensions.mirror_log`) so
each mirrored message's **every version is viewable as a render**, with an intuitive
version selector and **diff highlighting** between versions. CV2-focused (the rich
autoposts — weekly reset, trials, iron banner — are Components V2), noting when plain
text/embeds are present.

Phases **A+B are already shipped** (de-snowflaking via `followable_name` + the
`dest_server_id` config join → dest jump-links; source-channel filter; `client`-param
removal). This doc covers **C (capture)**, **D (render UI)**, **E (diffs)**.

## The core constraint
The `mirror_delivery` ledger stores **intent, never content** (fetched fresh at
delivery). So per-version renders need **new storage**, captured as the worker
materializes each version. There is **no backfill** — history starts at deploy.

## Capture point (confirmed)
`MirrorWorker._source_for` (`dd/beacon/mirror_worker.py:393`) already fetches +
item-emoji-rewrites the source into a complete `HMessage` (**content + embeds +
`components` (CV2) + attachments** — exactly what followers receive) **once per
`(src_msg_id, version)`**, on a cache miss (`_source_cache`, line 403). That is the
single natural snapshot point — one cheap write per materialized version, not per dest.
(Fast consecutive edits may skip a version number; we store the versions actually
materialized and label them by `#` + timestamp. This is honest and matches the ledger.)

The worker also has `msg.guild_id` here → capture the **source guild id** too, which
unlocks source-message links + a content summary on the run list (the one snowflake A
left bare).

## Data model — new table `mirror_message_version`
| col | notes |
|---|---|
| `src_msg_id`, `version` | PK |
| `captured_at` | naive-UTC |
| `src_guild_id` | for the source-message link (nullable) |
| `kind` | `"cv2"` \| `"classic"` — renderer branch |
| `summary` | short text label (first line / embed title) for the run list |
| `payload` | JSON snapshot (see below) |

**Payload format (reuse existing serialization):**
- **CV2** (`kind="cv2"`): the **raw component dicts** — same JSON shape the REST API
  uses, produced by `dd/anchor/cv2_raw.py::fetch_raw_message_components` /
  `dd/anchor/cv2_nodes.py` (`Node = dict`). This is the canonical, re-renderable,
  diff-able CV2 tree. (Capturing raw dicts at the worker may mean a raw components fetch
  alongside the HMessage, or serializing `HMessage.components` builders back to dicts —
  decide in C; raw-fetch is simplest and highest fidelity.)
- **classic** (`kind="classic"`): `{content: str, embeds: [embed dicts]}`.
- Attachments/stickers: store refs (filename/url) only, never binary.

**Retention:** extend `MirrorDelivery.prune` (or a parallel prune) to delete version rows
for pruned sources. Optionally cap `payload` size (truncate pathological posts).

## Renderer (the effort concentration — keep it bounded)
Recon finding: the **only** existing web/HTML post renderer is
`dd/anchor/hybrid_post_core.py::render_post_html` / `render_post_spec` — server-side
Python → **safe HTML string** (escaped leaves, whitelisted tags, http(s)-validated
URLs), injected client-side via `shared.js::initPostPreview` (`box.innerHTML`). But it
only understands the **flat hybrid-post markdown-body `PostSpec`**, NOT an arbitrary CV2
component tree. **There is no CV2-tree → HTML renderer yet.**

**Reuse vs build:**
- **Reuse** `render_post_html`'s inline bits — `_render_inline`, `_html_emoji_substituter`,
  `<t:…>` timestamps, `_render_buttons_html`, and its escaping/URL-whitelist discipline —
  for rendering **text-bearing nodes**.
- **Build (new, isolated module)** a CV2-tree walker: `container → card`, `section →
  row w/ accessory`, `text_display → inline-rendered markdown`, `separator → <hr>`,
  `media_gallery/thumbnail → <img>`, `button/link_button → disabled/anchor button`.
  Strict **"known node kinds → degrade to a labeled placeholder"** contract so it can't
  sprawl. Follow the existing server-render-then-`innerHTML` pattern (safe HTML string;
  the browser injects it) — do **not** hand-roll a client-side component renderer.
- **classic**: render markdown text + embed cards; since CV2 is the focus, keep this
  minimal and, per the requirement, **note when text/embeds are present** rather than
  chasing embed pixel-fidelity.

Endpoint shape (matches `/stats` + the preview pattern): the detail payload
(`/mirror-logs/data?src=<id>`) grows a `versions: [{version, captured_at, summary}]`
list; a new `GET /mirror-logs/render?src=<id>&v=<n>` (or `&v=<n>&diff=<m>`) returns the
**safe rendered HTML** for that version (optionally diff-marked vs version `m`). Renders
are pull/stateless — no live message, no lifecycle.

## Diffs
Diff at the **structured (node) level**, then render with diff markup:
- text nodes → word-level diff (added = green, removed = struck red);
- embed/text-display fields → per-field diff;
- media/buttons → a "changed" badge, not a visual diff.
Compute server-side alongside the render (produce diff-marked HTML for "vs previous"
mode) so the client stays a dumb injector. A small, well-tested diff util; cap to
text-bearing nodes.

## UI layout (inside the existing expandable run detail)
```
Versions:  ( v1 )( v2 )( v3• )      [ ✓ Highlight changes vs v2 ]
┌ render pane: <message as followers see it, for the selected version> ┐
Destinations: #chan → [Jump to message ↗]  DELIVERED  DONE     (A: done)
```
- Version chips (hover = timestamp); newest selected by default; click switches the
  render pane (fetches that version's HTML).
- Diff toggle appears for `v_n, n>1`; highlights vs `v_{n-1}`.
- The run-list source message can now show `summary` + a source-message link (from
  `src_guild_id`), retiring the last bare snowflake.

## Phasing (each independently shippable + tested)
- **C — capture (data only):** table + Atlas migration; snapshot in `_source_for`
  (incl. `src_guild_id`, `summary`, `kind`, `payload`); prune. No UI. `TEST_USE_MYSQL`
  lane for the new query/columns (naive-UTC + JSON on MySQL). Starts recording.
- **D1 — version selector + classic render** + "text/embeds present" note; CV2 shown as
  a structured fallback. Source-message summary/link on the run list.
- **D2 — CV2 tree → HTML renderer** (the big, isolated piece).
- **E1 — text/embed diff.**
- **E2 — CV2 node diff.**

## Open decisions to confirm before C
1. **Snapshot capture mechanism:** raw-components REST fetch at the worker (highest
   fidelity, one extra fetch per version) vs. serializing the already-built
   `HMessage.components` back to dicts (no extra fetch, relies on builder→dict fidelity).
2. **Render location:** server-side safe-HTML (recommended — reuses `render_post_html`
   discipline + existing `innerHTML` inject pattern) vs. client-side renderer.
3. **Diff placement:** server-side diff-marked HTML (recommended) vs. client-side diff.
4. **Payload size cap / truncation policy** for pathological posts.

## Risks
- **D2 (CV2 renderer)** is where a mess can grow — keep it one module with the strict
  degrade contract and golden-render tests per node kind.
- **Storage growth** — bounded by prune; CV2/embtarget JSON is small-to-moderate.
- **No backfill** — versions only from deploy forward; label old runs accordingly.
- Content is public announcement data (no privacy concern); render is XSS-safe because
  it comes from structured JSON through the escaping renderer, never raw HTML.

## Key references
- Capture: `dd/beacon/mirror_worker.py:393` (`_source_for`), `HMessage` shape
  `dd/hmessage/message.py` (`content/embeds/components/attachments`).
- Serialization: `dd/anchor/cv2_raw.py` (`fetch_raw_message_components`,
  `RawComponentBuilder`), `dd/anchor/cv2_nodes.py` (`Node` model + constructors).
- Reusable render bits: `dd/anchor/hybrid_post_core.py` (`render_post_html`,
  `_render_inline`, `_render_buttons_html`, `render_post_spec`); inject pattern
  `dd/anchor/web_static/shared.js::initPostPreview`.
- Page: `dd/anchor/extensions/mirror_log.py`, `dd/anchor/web_static/mirror_log.{html,css,js}`.
- Names/links (A, done): `dd/common/utils.py::followable_name`,
  `MirroredChannel.dest_server_id`.
