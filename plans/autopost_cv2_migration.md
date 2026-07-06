# Plan: migrate remaining fully-automatic anchor autoposts to Components V2

> **✅ CODE COMPLETE (2026-07-05).** Xûr, Lost Sector, Portal Ops migrated
> anchor-side (bodies → CV2, `cv2=True` on scheduler + control group) with their
> beacon `NavPages(cv2=True)` + CV2 no-data pages flipped in lockstep. `ruff`/`ty`
> clean, 539 tests pass. **Remaining before prod:** (1) dev runtime check per type —
> especially a **full-inventory Friday Xûr** (CV2's 4000-char cap is tighter than the
> embed's 4096; the code truncates + alerts, but confirm it fits); (2) enable
> `portal_ops` in the dev `FOLLOWABLES` (`"portal_ops": 1519232916549533726`) to test
> it there.
>
> **⚠️ TRANSITION-WINDOW CAVEAT (inherent, self-healing):** a navigator edits one
> message and can't toggle `IS_COMPONENTS_V2`, so while a followable's history window
> still mixes old embed posts with new CV2 posts, pressing prev/next **across the
> embed↔CV2 boundary errors** on that edit. Individual pages still render. Self-heals
> once the window fully rolls over to CV2: **~12 weeks** for Xûr (weekly, history 12),
> **~14 days** for Lost Sector / Portal Ops (daily, history 14). Consider timing the
> deploy or clearing old history if the transient breakage is unacceptable.


> **Status: ACTIVE (refreshed 2026-07-05).** Supersedes the 2026-07-01 stub, whose
> "which posts are embeds" list was stale (it named human-posted followables that
> the anchor does not autopost). Current-state below is from a fresh inventory. Do
> **one post type at a time**; no big-bang. Re-verify symbols before each step.
>
> This migration is the **gate** for `plans/convert_command_fold_into_edit.md`.

## Current state (verified 2026-07-05)

The anchor's fully-automatic (scheduled) autoposts are exactly five. Each is also
manually invokable via `/<name> send` — none is scheduled-only.

| Autopost | Scheduler | Cron (UTC) | Format today | Announcer |
|---|---|---|---|---|
| **Ada-1 shaders** | `ada.py:148` | `0 17 * * TUE` | **CV2 ✅ done** | `xur.api_to_discord_announcer` (`cv2=True`) |
| **Eververse** | `eververse.py:378` | `0 17 * * *` | **CV2 ✅ done** | `xur.api_to_discord_announcer` (`cv2=True`) |
| **Xûr** | `xur.py:640` | `0 17 * * FRI` | **Embed → migrate** | `xur.api_to_discord_announcer` (cv2 defaults False) |
| **Lost Sector** | `lost_sector.py:131` | `0 17 * * *` | **Embed → migrate** | `discord_announcer` (**no cv2 branch**) |
| **Portal Ops** (dormant¹) | `portal_ops.py:468` | `0 17 * * *` | **Embed → migrate** | `xur.api_to_discord_announcer` (cv2 defaults False) |

¹ Portal Ops is **live in prod** but **dormant in dev** — it only wires up when
`cfg.followables["portal_ops"]` is truthy, and dev's env leaves it `0`. **Enable it
in dev** (set the dev `portal_ops` followable/channel id in the dev env) so the
migration can actually be exercised before it reaches the prod-active feature. Since
it's prod-active, treat its CV2 migration with the same care as Xûr/Lost Sector.

**Migration targets: Xûr, Lost Sector, Portal Ops.** Ada + Eververse are the
templates to copy.

> **⚠️ Anchor-only migration is UNSAFE (verified 2026-07-05).** The beacon navigator
> (`/xur`, `/ls`, `/portal_ops`) edits **one** message across pages, and Discord
> forbids toggling `IS_COMPONENTS_V2` on an edit, so a navigator is **single-mode**
> (`dd/beacon/nav.py:243`). If the anchor posts a type as CV2 while its beacon
> `NavPages` stays `cv2=False`, the first prev/next press that crosses the embed↔CV2
> boundary (mixed history, or the embed no-data page) **errors**. Therefore each
> type's beacon `NavPages(cv2=True, no_data_message=<CV2>)` must flip **in lockstep**
> with the anchor change — it is required, not optional. The **mirror** is already
> CV2-aware (`dd/beacon/extensions/mirror.py:676,691,736,1062`) and needs no change.

## Shared plumbing (read first)

- `dd/anchor/autopost.py:30` `make_autopost_control_commands(..., cv2=)` — builds
  `/<name> auto|send|show`. The `cv2` flag makes the manual `send` placeholder and
  the `show` preview match the final format.
- `dd/anchor/extensions/xur.py:526` `api_to_discord_announcer(..., cv2=)` — used by
  Xûr/Ada/Eververse/Portal Ops. Posts a placeholder, retries the constructor, then
  **edits the placeholder in place** (`msg.edit(**hmessage.to_message_kwargs())`,
  `xur.py:607`); crossposts after 5s if `publish_message`. Discord **forbids
  toggling `IS_COMPONENTS_V2` on edit**, so the placeholder must already be CV2 —
  that's what threading `cv2=True` does (`xur.py:537-549`).
- `dd/anchor/extensions/lost_sector.py:38` `discord_announcer(...)` — used **only**
  by Lost Sector. **Creates a new message** (no placeholder/edit) via
  `utils.send_message(...)`. It has **no `cv2` parameter today** — see LS step 0.

## Per-post-type recipe (what Ada/Eververse did)

1. **Anchor constructor → CV2.** In the feature's `*_message_constructor` /
   `format_*_vendor`, build a `h.impl.ContainerComponentBuilder` (accent
   `h.Color(cfg.embed_default_color)`; `.add_text_display(...)`,
   `.add_separator(divider=True)`, media galleries for images) and return
   `HMessage(components=[container])` instead of `HMessage(embeds=[embed])`.
   Template: `dd/anchor/extensions/ada.py:120-140` /
   `dd/anchor/extensions/eververse.py:344-375`.
2. **Flip the announcer to CV2.** Pass `cv2=True` where the scheduler and the
   control group are wired (`make_autopost_control_commands(..., cv2=True)` and the
   `api_to_discord_announcer(..., cv2=True)` in the `StartedEvent` listener).
3. **Beacon navigator (REQUIRED — see the ⚠️ callout above).** Flip the followable's
   `NavPages(cv2=True, no_data_message=<CV2 container>)`:
   - xur — `dd/beacon/extensions/xur.py:82-100` (convert its explicit embed
     `no_data_message` to a CV2 container).
   - lost_sector — `dd/beacon/extensions/lost_sector.py:100-108`.
   - portal_ops — `dd/beacon/extensions/portal_ops.py:57-64` (uses `ResetPages`;
     `preprocess_messages` already short-circuits CV2 at `nav.py:757`, no change).
   Rendering itself is already format-agnostic (`nav.py:361-381`,
   `HMessage.from_message` rebuilds CV2 via `rebuild_components`), so only the
   single-mode `cv2` flag + no-data page need flipping.
4. **Mirror** already handles CV2 (`_is_cv2` + `rebuild_components`) — no change
   for text/section posts. If a post uses media/sections, check
   `plans/rebuild_components_media_support.md`.

## Per-target notes

- **Xûr** (`xur.py:417 format_xur_vendor`) — biggest body: location, exotic
  armor/weapons/catalysts, legendary sets/weapons fragments, plus `XUR_FOOTER` and
  a `set_image(cfg.xur_image_url)`. The image → a full-width **media gallery** item.
  Watch the CV2 length limits (`_CV2_LIMIT_HINT`) — Xûr is the longest post; may
  need to split across text displays / a container that stays under the component
  cap. This is the one most likely to hit Discord's CV2 size ceiling — test with a
  full-inventory Friday sample.
- **Lost Sector** — **step 0: give `discord_announcer` a `cv2` branch** mirroring
  `api_to_discord_announcer` (CV2 placeholder-less create is fine since it makes a
  new message, but the control group's `send`/`show` still need `cv2=True` to match).
  Body is built by shared `dd/common/lost_sector.py:154 format_post` (embed with
  `set_image(ls_gif_url)`) — convert there; the gif → media gallery. Also the
  `LsUpdate` message command (`lost_sector.py:108`) edits an existing LS post in
  place — once posts are CV2 it must edit CV2 (and old embed LS posts can't be
  edited to CV2; that command only targets fresh posts). **Beacon lookahead:** the
  navigator generates 7 forward days itself (`lookahead_len=7`) via the *same*
  `format_post`, plus a KeyError no-data branch (`dd/beacon/extensions/lost_sector.py:84-95`)
  that must also go CV2 — else navigating forward from a CV2 "today" page breaks.
  Migrating `format_post` covers both the anchor post and the beacon lookahead.
- **Portal Ops** (`portal_ops.py:403/429`) — straightforward embed→container like
  Eververse; can't verify on dev while dormant, so land it but flag as untested.

## Verification (per type, on dev)

Deploy dev (`make deploy-anchor-dev`). Trigger `/<type> send` (and `show`): posts as
CV2, crossposts in the announcement channel. Beacon `/<type>` navigator shows current
+ past posts as CV2, paginates, disables on timeout; mirrors still fan out.
`uv run ruff check`, `uv run ty`, `uv run python -m pytest`. **Never deploy prod
without explicit user confirmation.**
