# Plan (STUB): migrate remaining embed autoposts to Components V2

> **Status: DEFERRED stub (2026-07-01).** Unblocked by the embed/CV2 dual-support work on
> `feat/nav-cv2-dual-support` (`dd/beacon/nav.py` + `HMessage` now render both embed and
> CV2 pages). Do one post type at a time; no big-bang. Re-verify symbols before executing.

## Context

Only the **eververse** autopost is Components V2 today; the rest are still embeds
(xur, gunsmith, lost_sector, portal_ops, nightfall, trials, twab, iron_banner, ada,
weekly_reset, free_games, emblems_and_cosmetics). The navigator now supports both, so any
post type can move to CV2 independently. This is the incremental path toward the eventual
"all-CV2" end-state (see the decision brief that produced the dual-support work).

## Per-post-type recipe (what eververse did)

1. **Anchor side** — build the post as CV2 in its message constructor: return
   `HMessage(components=[container])` (use `dd/common/components.py:build_container` /
   `h.impl.ContainerComponentBuilder`) instead of `HMessage(embeds=[…])`. Pass `cv2=True`
   to the announcer (`xur.api_to_discord_announcer` / `make_autopost_control_commands`) so
   the placeholder matches the final type (Discord forbids toggling `IS_COMPONENTS_V2` on
   edit). Eververse is the template (`dd/anchor/extensions/eververse.py`).
2. **Beacon navigator** — add `cv2=True` to that followable's `setup_nav_pages(...)` (in
   `dd/beacon/extensions/<type>.py`), so past posts + the no-data page render as CV2.
3. **Mirror** already handles CV2 (`_is_cv2` + `rebuild_components`) — no change, but if
   the post uses media/sections see `plans/rebuild_components_media_support.md`.

## Hard cases (embed features with no clean CV2 equal — plan these individually)

- **statistics** (`dd/beacon/extensions/statistics.py`) uses embed `add_field` (tabular) —
  needs a text-layout rethink.
- **/post embed** builder + author/footer **icons**, thumbnails, `set_image` — image →
  media gallery; icons → text/thumbnail.
- **twab** navigator does heavy embed manipulation (`dd/beacon/extensions/twab.py`).

## Verification (per type)

Deploy dev; the autopost posts as CV2; `/<type>` navigator shows current + past CV2 posts,
paginates, disables on timeout; mirrors still fan out. `uv run ruff/ty/pytest`.
