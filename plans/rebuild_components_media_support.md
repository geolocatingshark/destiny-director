# Plan (STUB): extend rebuild_components for media galleries / sections / etc.

> **Status: DEFERRED stub (2026-07-01).** Do this when a CV2 post that uses media (images)
> or sections needs to be **re-rendered from a fetched message** — i.e. mirrored, or shown
> in a navigator. Not needed while CV2 posts are text-only containers (eververse).

## Context

`dd/common/components.py:rebuild_components` / `_rebuild_component` turn fetched CV2
component **models** back into sendable **builders**. Today it only handles
`Container` / `TextDisplay` / `Separator` and **raises `NotImplementedError`** on anything
else (media galleries, sections, thumbnails, files, buttons).

Two consumers depend on it and currently degrade on unsupported types:
- **Mirror** (`dd/beacon/extensions/mirror.py`) — a CV2 source with media would fail to
  mirror (raises).
- **Navigator** (`dd/hmessage/message.py:from_message`) — now catches the
  `NotImplementedError` and captures **no components**, so such a post shows as a blank
  "no data" page in its `/<type>` navigator.

## Work

Extend `_rebuild_component` to round-trip the remaining CV2 content types actually emitted:
- `MediaGalleryComponent` → `h.impl.MediaGalleryComponentBuilder` + `MediaGalleryItemBuilder`
  (hikari 2.5 has these; see `plans/... cv2-post-image-editing` notes / memory
  `cv2-post-image-editing-shelved` for the media-builder API).
- `SectionComponent` (+ `ThumbnailComponent` accessory), `FileComponent`, and interactive
  rows if/when a post uses them.
Keep the "raise on genuinely-unknown type" behaviour so a new kind still surfaces loudly.

This is also a **mirror correctness win** (media CV2 posts become mirrorable).

## Verification

Unit round-trip tests in `dd/common/tests/` (model → `rebuild_components` → builder →
re-`build()` equals the original shape) for each newly-supported type; a CV2 post with an
image mirrors and shows in its navigator. `uv run ruff/ty/pytest`.
