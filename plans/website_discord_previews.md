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
- **Scope = all rotations:** every rotation/command gets a web editor **and** a preview, in
  one pass — not incrementally, not only the two that already have forms.
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

## Per-rotation authoring reality — CONFIRM before building editors

"Every rotation gets an editor + preview" needs a per-rotation call, because they differ in
what's authorable. **Resolve each of these before Part B** (they change the shape of the
work — a full create/edit/publish form vs. a preview bolted onto the existing rotation
editor vs. a read-only preview):

- **lost_sector** — rotation is DB-only, already edited via the **existing rotation editor**
  (`extensions/rotation_editor.py` + `editor.html/js`) and `/rotation edit`. So it does NOT
  need a *new* form; it needs a **post preview** attached to the rotation editor (or a small
  read-only preview page). Decide which.
- **legacy_activities** (`dd/beacon/extensions/legacy_activities.py`) — schedule/rotation
  data, auto-seeded. Same question: is it edited via the rotation editor already, or does it
  need its own form? Preview is the valuable part.
- **xur** (`extensions/xur.py`) — **fetched live from the Bungie API**; there is nothing to
  author. An editor makes no sense; a **read-only preview** of the current/upcoming Xûr post
  is the only meaningful surface. `initPostPreview` supports this fine (a preview page with a
  fixed/empty `readForm`), but there's no form/create/edit/publish.
- **weekly_reset, trials** — already have full forms + previews; they just **migrate** onto
  the generic endpoint (no behavior change).

Net: this is less "build 3 identical forms" and more "one generic preview surface, wired to
each rotation in whatever authoring mode that rotation actually supports." **This is the main
open design decision and should be settled first.**

## Part A — generic spec + generic `/preview` endpoint (CV2 only)

1. Define a JSON-safe **`PostSpec`** (start CV2-only): `{kind:"cv2", body:str,
   image_url:str|None, accent_color:str|None}`. Leave room for a future
   `{kind:"embed", title, description, fields[], thumbnail_url, image_url, color, author,
   footer}` variant (Part C) — design the type as a tagged union from day one.
2. Refactor `render_post_html` to accept a `PostSpec` and dispatch on `kind` (only `cv2`
   implemented now; `embed` raises/430s until Part C). Keep the existing string→HTML
   machinery as the `cv2` branch.
3. Add one generic route — `POST /post/preview` — taking `PostSpec` JSON directly, decoupled
   from `HybridPostSpec.context_from_payload`/`build_body` and from the create/edit/publish/
   `DraftMeta` lifecycle (lighter consumers like previews-only pages and user-commands don't
   want that lifecycle).
4. Migrate weekly_reset + trials: their `readForm()` still returns producer fields, but the
   client sends to `/post/preview` with the producer emitting a `PostSpec` (its `build_body`
   result wrapped as `{kind:"cv2", …}`). Keep the old per-producer routes working during
   migration, then remove. **Manual smoke both forms after** (no JS tests yet — see
   `plans/js_unit_tests.md`).

## Part B — per-rotation preview surfaces (after the CONFIRM above)

For each of lost_sector / legacy_activities / xur, in the authoring mode decided above:
attach a preview (`initPostPreview` pointed at `/post/preview`) driven by that rotation's
`build_body`→`PostSpec`. Where a rotation is authorable (lost_sector/legacy via the rotation
editor), wire the preview live to the editor's current values; where it's read-only (xur),
render the current/upcoming post. Reuse the `.form-page`/preview CSS already in `shared.css`.

## Part C — DEFERRED: embed render + user-commands

When `plans/website_user_commands.md` is built:
- Add the `{kind:"embed", …}` branch to `render_post_html`/`PostSpec` (safe-HTML mirror of
  `embeds_to_container`'s mapping: title/description → heading + body, fields → labelled
  blocks, thumbnail/image → `post-image`, color → the `#previewBox` accent bar, author/footer
  → small text).
- user-commands' web manager consumes `initPostPreview` + `/post/preview` with an `embed`
  (or `cv2`, or copied-message) spec per its `response_type`.

## Open questions (resolve as you go)

1. **Per-rotation authoring mode** (the big one — see "Per-rotation authoring reality"):
   editor-attached preview vs. new form vs. read-only, for each of lost_sector /
   legacy_activities / xur.
2. Does `PostSpec` live in `hybrid_post_core`, `dd/hmessage`, or `dd/common/components`?
   (It's the cross-cutting type; `dd/common` avoids an anchor→producer coupling.)
3. Should the generic `/preview` also serve read-only previews of *already-posted* rotations
   (fetch the live post → `HMessage` → `PostSpec`), which would give xur/legacy a preview for
   free without re-deriving `build_body`? (Ties to `HMessage` serialization.)
4. Branch/PR shape: Part A is one PR; Part B is per-rotation PRs; Part C rides with
   user-commands.
