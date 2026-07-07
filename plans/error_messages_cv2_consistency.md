# Plan: make all anchor user-facing messages CV2 and style-consistent

> **Status: DEFERRED / SCOPED (2026-07-07).** Not done in the CV2-migration session —
> explicitly deferred to a **later phase**. Scope locked with the user this session:
> - **Approach: the full sweep** below (convert every plain-text/embed error, success
>   and confirmation to the shared CV2 helpers; delete `_error_embed`; one color set).
> - **INCLUDE `dd/common/controller.py`** — normalize the shared confirm/timeout
>   dialogs to the one color set. NB: `controller.py` is `dd/common`, so this also
>   changes the **beacon** bot's surface, not just anchor.
> - **EXCLUDE the `/post` embed command and the right-click embed context-menu
>   commands** ("Convert to components" / "Edit components" / "Copy embed"): those
>   embed authoring surfaces are slated for a **separate later phase-out**, so do not
>   CV2-ify their responses now. The interactive embed-builder UI (`embeds.py` /
>   `cv2_builder.py`) chrome is part of that same phase-out — also out of scope here.
>
> Goal (unchanged): every remaining response the anchor bot shows a user (errors,
> successes, confirmations, progress) is **Components V2** and follows **one** style,
> whether the code path historically used plain text, an embed, or CV2. Original
> readiness notes (2026-07-05) below.

## The problem (from the 2026-07-05 audit)

Anchor responses are currently a three-way mix, sometimes **within the same file**:

1. **Plain text** — `ctx.respond("some string")`. E.g. autopost enable/disable &
   `send` (`autopost.py:58-100`), post-embed errors as bold labels
   (`posts.py:45-57`), most CV2-edit guards (`posts.py:155,158,182,287,330`), bungie
   login, rotation editor, ls settings, `/source_code`, `/<bot> info`.
2. **Embed** — via `_error_embed(...)` (⚠️ + red, `posts.py:135`) and ad-hoc
   `h.Embed(description="✅ …")` successes (`posts.py:231,272,313`); controller
   confirms (`controller.py:79-134`); the embed-builder UI (`embeds.py`).
3. **CV2** — `build_container(["⚠️ …"])` in the Convert flow (`posts.py:392-461`),
   the `show` cv2 branch (`autopost.py:114-133`), `/help`
   (`help.py`), CV2 builder UI (`cv2_builder.py`).

Concrete inconsistencies to kill:
- **Three error stylings coexist** — `_error_embed` (embed), bold-label plain text,
  and CV2 `build_container(["⚠️ …"])`. `posts.py` alone uses all three;
  `_load_cv2_nodes` even uses an embed at `:173` and plain text at `:182`.
- **Success markers vary**: `✅` on embed/CV2 successes but nothing on plain-text
  ones ("Announced", "Post updated", "Successfully logged in").
- **Four color palettes**: `_ERROR/_SUCCESS_COLOR` (`posts.py:125`),
  `_WARN/_DANGER/_NEUTRAL_COLOR` (`controller.py:50`), `cfg.embed_*_color`,
  `discord_logging` styles.
- **`_error_embed` is anchor-local** (`posts.py:135`), so other extensions reinvent
  errors as plain text instead of reusing it.
- **Silent uncaught errors**: `_report_uncaught_command_error`
  (`discord_logging.py:520`, registered `__main__.py:68`) logs to the alerts channel
  and returns `True` to suppress the traceback but **never replies to the invoker** —
  an unhandled exception shows the user nothing.
- **Ephemeral inconsistency**: many owner-only errors are ephemeral, but autopost
  toggles/`send`, `ls_settings`, xur toggle, `/source_code`, `/<bot> info`, `/help`
  are not (some intentionally public — decide per case, don't blanket-flip).

## Target: one shared CV2 response toolkit

Add helpers next to `build_container` in **`dd/common/components.py`** (shared, so
every extension uses the same thing — this is the natural consolidation point the
audit points to):

- `cv2_error(title, body)` → CV2 container, danger accent, `⚠️`/`🛑` convention.
- `cv2_success(body)` → CV2 container, success accent, `✅`.
- `cv2_notice(body)` / `cv2_progress(body)` → neutral accent, for
  confirmations / "Doing X…" progress.
- Define **one** color set (danger / success / warning / neutral) as module
  constants and delete the scattered `_ERROR/_SUCCESS_COLOR` (`posts.py`) and reuse
  in `controller.py` (or leave controller's as-is if that bot surface is out of
  scope — decide with the user; controller is `dd/common`, shared by both bots).

Then sweep call sites:
- Replace every `_error_embed(...)` with `cv2_error(...)`; delete `_error_embed`.
- Replace ad-hoc `h.Embed(description="✅ …")` successes with `cv2_success(...)`.
- Replace plain-text errors/successes (`posts.py:45-57,84-112,155-182,287,330`,
  `autopost.py:58-100`, `lost_sector.py:95-128`, `xur.py:677-686`, bungie, rotation
  editor) with the helpers. Keep genuinely informational multi-line text (e.g.
  `/<bot> info`, `/source_code` code block) as-is unless the user wants those CV2 too
  — note them as judgment calls.
- **Centralize uncaught-error UX**: in `_report_uncaught_command_error`, after
  logging, best-effort reply to the invoker with a generic `cv2_error("Something
  went wrong", …)` (ephemeral) — guarding the already-responded / interaction-expired
  cases. This is the single highest-value fix: it converts silent failures into a
  consistent visible error.

## Scope decisions to confirm with the user

- **Interactive builder surfaces** (`embeds.py` embed-builder UI, `cv2_builder.py`)
  — the embed builder is inherently about authoring embeds; leaving its *own* UI as
  embeds may be fine. Confirm whether "all messages CV2" includes builder chrome.
- **`dd/common/controller.py`** confirms/timeouts are shared with the beacon bot —
  confirm whether this pass is anchor-only or should also normalize controller.
- **Post bodies vs. responses**: this plan covers *responses* (errors/successes/
  confirmations). Autopost *bodies* (embed → CV2) are `autopost_cv2_migration.md`.

## Verification

- No behavior tests exist for these responses today; add a couple of unit tests for
  the new `dd/common/components.py` helpers (they're pure builders, easy to assert on
  `.build()` output) under `dd/common/tests/`.
- Manual: trigger each error path (wrong-owner, forbidden edit, bad request, uncaught
  exception) and confirm a uniform CV2 error appears.
- `uv run ruff check` (watch organizeImports stripping now-unused `_error_embed` /
  `h.Embed` imports), `uv run ty`, `uv run python -m pytest`.
