# Website Discord previews — generic spec-driven previewer + per-rotation editors

> **Status (2026-07-18): client previewer DONE; this plan is the remaining server-side +
> rollout work, now fully scoped.** `initPostPreview({routePrefix, readForm, accentColor})`
> is extracted into `dd/anchor/web_static/shared.js` (branch `anchor/share-web-form-client`,
> shipped to dev) and consumed by the weekly_reset + trials forms. `embeds_and_cv2_parallel_
> first_class.md` is **done** (commit `704e262`; `/post` Edit/Copy/Convert dispatch embed vs
> CV2 by sniffing a live message). This plan supersedes the old "blocked" framing.

## Decisions locked in (owner, 2026-07-18)

- **Generic previewer:** one previewer + one spec-driven `/preview` endpoint that renders
  *any* post; reuse the same code for all posts.
- **Scope = all rotations, as a scrolling preview wall (NOT editors):** every rotation gets a
  read-only **"scrolling view of several upcoming posts"** (this week, next week, …) — a
  forward-looking preview feed, not date-wise editors of the rotation data. See Part B.
- **CV2 render now, embed render later:** ship the generic spec + endpoint rendering **CV2
  only** first (the only current producers are CV2). Add the embed→safe-HTML branch when the
  user-commands manager (`plans/website_user_commands.md`) is actually built, since
  user-commands is the first real embed consumer (`response_type 3` → `h.Embed`).

## Why (architecture facts that shape this — verified against the code)

- **No serializable "post spec" exists.** `HybridPostSpec` (`hybrid_post_core.py`) is a bag
  of per-producer Python closures (`build_body`, `context_from_payload`, …) bound at import,
  not a wire format. `HMessage` (`dd/hmessage/message.py`) is the natural runtime "one post,
  either format" value (holds `embeds` XOR `components`, mutually exclusive) but is **not
  JSON-serializable** and is **not used in the preview path**.
- **`render_post_html` is CV2-shaped, string-driven.** It renders a single markdown *string*
  (`spec.build_body(ctx)`) + optional image into safe HTML (`{strong,em,span,a,img}`,
  escaped leaves, http(s)-validated URLs, `:emoji:`/`<t:…>` handling). It mirrors
  `build_cv2`'s one-text-display + media + footer layout. It has **no** embed structure
  (title/fields/thumbnail/color/author). `dd.common.components.embeds_to_container()` is the
  existing embed→CV2 mapping to crib from when the embed branch is built.
- **`/preview` is generic in code, per-producer in invocation.** `hybrid_post_core.preview()`
  is one function, but always called with a producer-bound `spec`; the two routes
  (`/weekly_reset/preview`, `/trials/preview`) exist only so the client can build a URL.
- **Client `initPostPreview` is already format-blind** — POSTs `readForm()`, injects
  returned safe HTML (`innerHTML` on ok, `textContent` on error). **No client changes
  needed** for a generic endpoint; only the `routePrefix`/payload target.

## The reusable core vs. per-consumer endpoints (design decided)

The genuinely reusable "one previewer" is a **pair**, not a single HTTP endpoint:
- **client `initPostPreview`** (done) — format-blind; POSTs a payload, injects safe HTML.
- **server `render_post_spec(spec: PostSpec, emoji_dict) -> safe HTML`** (Part A) — the one
  render path every consumer shares.

The HTTP endpoints ON TOP are thin adapters over `render_post_spec`, and different consumers
need **different adapter shapes** — so each endpoint ships with its consumer rather than one
speculative `/post/preview` up front:
- **weekly_reset / trials** — form fields → server `context_from_payload` → `build_body` →
  `PostSpec.cv2(...)` → `render_post_spec`. The body is derived **server-side** (needs the
  manifest + business rules), so the client can't author a spec; these keep their existing
  `/{prefix}/preview` routes, now flowing through `render_post_spec` internally.
- **Part B rotations** — **server-generated**: the client asks "give me the next N posts for
  rotation X"; the server computes each future post's context → `build_body` → `PostSpec` →
  `render_post_spec`, and returns N rendered blocks. A **feed** endpoint, not a client spec.
- **Part C user-commands** — the user authors an embed / message content directly, so it POSTs
  a **client-authored `PostSpec`** and renders it. THIS is the natural `/post/preview` spec
  endpoint — it ships with user-commands.

So Part A builds the reusable core; the standalone `/post/preview` spec-POST endpoint is
deferred to its first real consumer (Part C), and Part B adds its own feed endpoint.

## Part A — generic PostSpec + shared render path (CV2 only)  ← IN PROGRESS

1. Define a JSON-safe **`PostSpec`** tagged union (CV2 now): `{kind:"cv2", body:str,
   image_url:str|None}`, with an `embed` variant reserved for Part C. Lives in
   `hybrid_post_core` for now (all current consumers are anchor; the anchor web app serves
   every preview surface, including the future user-commands manager).
2. Add **`render_post_spec(spec, emoji_dict)`** dispatching on `kind` — the `cv2` branch calls
   the existing `render_post_html` string→HTML machinery unchanged (so its direct callers and
   tests keep working); `embed` raises "not yet" until Part C.
3. Reroute `hybrid_post_core.preview()` to wrap `build_body` output as `PostSpec.cv2(...)` and
   render via `render_post_spec` — so weekly_reset + trials now flow through the one shared
   render path (no external behavior change). **Manual smoke both forms after.**
4. `PostSpec.from_payload` (for the future spec-POST endpoint) + unit tests for the dispatch.

## Part B — "scrolling wall of upcoming posts" (read-only, server-generated)

**Not** date-wise editors of the underlying rotation data — a **forward-looking preview
feed**: for each rotation, a page showing several upcoming posts (this week, next week, …)
rendered exactly as Discord will show them, scrollable into the future. Read-only; no
create/edit/publish, no per-rotation form. This sidesteps the authoring-mode problem entirely
(xûr is API-fetched, lost_sector/legacy are DB rotations — none need a *web form* just to be
previewed).

- Server: a feed endpoint per rotation (or one generic `GET /preview/<rotation>?count=N`) that
  computes the next N periods' contexts → `build_body` → `PostSpec` → `render_post_spec`, and
  returns N rendered blocks (+ each post's target date/label).
- Client: a small scroll view that lays the blocks out vertically with date headers. Reuse the
  `#previewBox` / preview CSS already in `shared.css`.
- Each rotation supplies a "context for period T" hook (lost_sector/legacy read their rotation
  row for that date; xûr shows current + any known upcoming). weekly_reset/trials can join the
  same wall for their own future posts.

## Part C — DEFERRED: embed render + user-commands

When `plans/website_user_commands.md` is built:
- Add the `{kind:"embed", …}` branch to `render_post_html`/`PostSpec` (safe-HTML mirror of
  `embeds_to_container`'s mapping: title/description → heading + body, fields → labelled
  blocks, thumbnail/image → `post-image`, color → the `#previewBox` accent bar, author/footer
  → small text).
- user-commands' web manager consumes `initPostPreview` + `/post/preview` with an `embed`
  (or `cv2`, or copied-message) spec per its `response_type`.

## Open questions (resolve as you go)

1. ~~Per-rotation authoring mode~~ — RESOLVED: no editors; Part B is a read-only scrolling
   wall of upcoming posts (server-generated).
2. `PostSpec` home — Part A puts it in `hybrid_post_core` (all preview surfaces are served by
   the anchor web app). Revisit only if a non-anchor module ever needs to construct it.
3. Part B "context for period T" hook — how far into the future each rotation can compute
   (lost_sector/legacy: as far as the rotation table has rows; xûr: current + whatever the
   API exposes upcoming). Decide N and the empty-future behavior per rotation.
4. Should Part B also/instead render already-posted periods by fetching the live post →
   `HMessage` → `PostSpec` (needs `HMessage` JSON serialization), vs. always re-deriving via
   `build_body`? Re-deriving is simpler and consistent with the forms; live-fetch is truer to
   what's actually posted. Lean re-derive unless drift is a concern.
5. Branch/PR shape: Part A one PR (this branch, `anchor/generic-post-previewer`); Part B one
   PR (the wall + per-rotation context hooks); Part C rides with user-commands.
