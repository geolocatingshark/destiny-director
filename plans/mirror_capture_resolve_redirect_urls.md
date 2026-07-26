# Mirror version capture — resolve redirect/short-link URLs before storing (LOW PRIORITY)

**Status:** deferred / filed for later. Owner-reported 2026-07-26.

## Problem

`MirrorMessageVersion` snapshots store media URLs (media-gallery items, thumbnails,
embed images) **exactly as they appear in the source message** — the raw string. When a
producer uses a **redirect / short link** for an image (e.g. `kyber3000.com/<slug>` that
302s to the real CDN asset), we store the short link, not the final resolved URL.

That breaks version diffing in one specific case: if an edit changes only the *target*
of a short link (same short URL, but the infographic behind it was swapped), both
versions store the **identical** URL string. So:

- the version diff (`cv2_render.render_diff`, which compares stored node dicts) sees **no
  change** — a genuine, intentional image change is invisible;
- the render for both versions points at the same short link, which now resolves to the
  new image, so old versions silently show the new asset.

## Why it matters

Announcement edits sometimes update *only* the artwork behind a stable short link. The
whole point of the version log is to show what changed per operation; a swapped
infographic behind a short link is a real content change that currently can't be seen or
diffed.

## Where

- Capture: `dd/beacon/mirror_worker.py` — `_snapshot_payload()` / `_capture_version()`
  (serializes `hmsg.components` via `build()[0]`, or embeds via the entity factory).
  The media URLs live inside those built dicts:
  - media gallery: node `type 12` → `items[].media.url`
  - thumbnail: node `type 11` → `media.url`
  - (classic) embed image/thumbnail: `image.url` / `thumbnail.url`
- The diff that would then detect the change already works at the node level
  (`cv2_render._diff_nodes` / `_node_key`) — a differing final URL makes the media node
  differ, so it surfaces as a removed+added tile. No renderer change needed.

## Proposed fix

At capture time (best-effort, off the delivery critical path — capture is already
fire-and-log-only), walk the built payload and **resolve each media URL by following its
redirect chain** to the final URL, then store the resolved URL.

- Use an async HEAD (fall back to a ranged GET) with redirect-following and a short
  timeout; on any failure/timeout, **keep the original URL** (never break capture).
- Cache within a single capture pass (dedupe identical URLs).
- Only resolve http(s) URLs; skip `attachment://` and already-CDN (`cdn.discordapp.com`)
  URLs (no redirect, save the round-trip).
- Cost: one extra request per distinct media URL, once per captured version (not per
  destination) — acceptable for the low frequency, but keep the timeout tight.

## Stronger alternative (if redirect resolution proves insufficient)

Store a **content hash** (e.g. of the fetched bytes, or an ETag/Last-Modified) alongside
the URL. This also catches the case where a *stable* CDN URL serves changed content over
time (redirect resolution wouldn't). Heavier (must fetch the image body); only do this if
the redirect-resolution approach misses real cases.

## Risks / notes

- No backfill — only new captures get resolved URLs; existing rows keep short links.
- Redirect resolution adds a network dependency to capture; must stay strictly
  best-effort (swallow + log, like the rest of `_capture_version`).
- Some short-link providers rate-limit HEAD; the GET fallback + timeout must be gentle.
