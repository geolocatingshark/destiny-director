# Trials of Osiris hybrid post — reconciliation onto current dev

## Context

The Trials hybrid producer (draft → owner web form → publish → beacon-mirror) was built
on branch **`feat/trials-hybrid-post`**, cut from **`dev @ c99074b`**, by extracting a
shared **`dd/anchor/hybrid_post_core.py`** out of `weekly_reset` and building
`extensions/trials.py` on it. It passed `make check` (738 tests) at the time.

Since then **dev advanced to `b7e1632`** and, crucially, commit **`16db44a`
re-architected the very `weekly_reset` internals the shared core extracted**. A mechanical
`git rebase dev` therefore conflicts *semantically* (not just textually) and was aborted.
This plan specs the **reconciliation**: re-derive the shared core + `weekly_reset`
extraction against dev's current model, and update Trials (and its form) to match, so the
branch lands cleanly on dev with the invariant intact.

## What changed on dev (`c99074b` → `b7e1632`) that forces the rework

`16db44a fix(anchor): edit current week's weekly-reset post; split form actions`:
- **Route model:** `/save` + `/publish` are **replaced by `/create` + `/edit`**, each
  taking a `payload["publish"]` flag, behind a shared **`_post_action(payload, *,
  create)`**. Create refuses (409) if a current-week post exists; Edit refuses (409) if
  none. Publish = strict validate + crosspost (**blocking `problems`**); plain post/edit =
  advisory `warnings`, **but a failed send/edit is now a blocking `problem`** (no false
  "done ✓"). Routes are now `GET /weekly_reset`, `POST /{create,edit,preview,delete,auto}`.
- **`DraftMeta` gained `reset_ts: int = 0` (wall-clock stamp) + `is_current(reset_ts)`**;
  `to_dict`/`from_dict` carry it. A new **`_send_new_post`** helper stamps `meta.reset_ts
  = current_reset_ts()` on first post; `post_or_edit_unpublished` + `publish_draft`'s
  fallback use it; **delete resets `reset_ts = 0`**.
- **Form GET** keys the draft/buttons off `meta.is_current(current_reset_ts())`; bootstrap
  now sends **`post_this_period`** (not `posted`) + `crossposted`.
- **`weekly_reset_form.{js,html}`** now have **four post buttons** (Create, Create &
  publish, Edit, Edit & publish) + Delete, driven by a `postAction(path, publish)` helper
  hitting `/create` or `/edit`; visibility from `post_this_period`/`crossposted`.
- `b62e047` added a "back to control panel" link to every web page; `d9f55b0` reformatted.

Net: my core's `save`/`publish` route functions and old `DraftMeta` are obsolete; Trials'
form + routes were built on the old `/save`+`/publish` model.

## Invariant (keeps dev's weekly_reset green)

Dev's `test_weekly_reset.py` is now **63 tests**; every `wr.<name>` it uses must remain a
`weekly_reset` attribute (move body to core, keep the name in WR). New/again-required
surface it exercises: `wr._handle_create` / `_handle_edit` / `_handle_delete` /
`_handle_auto` / `_handle_form_get`, `wr.DraftMeta` (with `reset_ts` + `is_current`),
`wr.post_or_edit_unpublished`, `wr.publish_draft`, `wr.render_post_html`,
`wr._format_reset_ts`, `wr._discord_error_note`, `wr.build_cv2`, plus all the local
render/validate/apply/classifier symbols. It does **not** reference `_post_action` or
`_send_new_post` by name (tested via `_handle_create`/`_handle_edit`), so those may live in
the core.

## Decisions (confirmed)
- Rewards = hardcoded static; Bonus Focus Pool = manifest-linked weapons; dedup via the
  shared core. **Trials adopts dev's Create/Edit (± publish) model** for consistency (its
  form gets the same four buttons + Delete). `is_current(current_reset_ts())` fits Trials'
  Fri→Tue weekend: a Friday post's stamped Tuesday-boundary stays "current" until the next
  Tuesday reset, then the form offers Create for the new weekend.

## Execution: land the branch on dev
1. **Bring new files forward, reset onto dev.** `feat/trials-hybrid-post` → reset to
   `dev` (`b7e1632`); re-add the branch's net-new files from the old tip (`git checkout
   <oldtip> -- dd/anchor/hybrid_post_core.py dd/anchor/extensions/trials.py
   dd/anchor/web_static/trials_form.{html,js,css} dd/anchor/tests/test_trials.py
   plans/trials_hybrid_post.md`), then apply the reconciliation edits below and commit
   fresh (single clean history, no broken intermediate commits).

## Core changes — `dd/anchor/hybrid_post_core.py`
- **`DraftMeta`**: add `reset_ts: int = 0` + `is_current(reset_ts) -> bool` (`message_id
  != 0 and self.reset_ts in (0, reset_ts)`), and `reset_ts` in `to_dict`/`from_dict`
  (legacy default 0). Port dev's docstrings verbatim.
- **Publish path**: add `_send_new_post(bot, hmessage, channel_id, meta)` stamping
  `meta.reset_ts = current_reset_ts()`; `post_or_edit_unpublished` + `publish_draft`'s
  fallback call it. (Core already owns `current_reset_ts`.)
- **Route layer** — replace `save`/`publish` with dev's model, spec-driven:
  - `post_action(spec, payload, bot, *, create)` = the full `_post_action` body,
    parameterized (`spec.context_from_payload/validate/save_draft/save_meta/load_meta/
    persist_default_image/channel_id`, and `publish_draft`/`post_or_edit_unpublished`
    which already take `spec`). Uses `current_reset_ts()` + `meta.is_current(...)`; 409
    guards; publish→blocking problems, plain→advisory warnings + blocking 502 on failed
    send; returns `{ok, note, warnings, post_this_period, crossposted}`.
  - `form_get`: key on `meta.is_current(current_reset_ts())` (load saved draft only when
    current, else `spec.build_context()`); bootstrap via `spec.build_bootstrap(draft,
    meta)` (which now includes `post_this_period`).
  - `delete`: reset `reset_ts = 0` alongside the existing reset.
  - `preview`/`auto`: unchanged.
  - Keep `_discord_error_note`, `preview_emoji_dict`, the weapon pool/resolver as-is.
- `HybridPostSpec` fields are largely unchanged; the old `save`/`publish` core route fns
  are removed in favour of `post_action`.

## `weekly_reset.py` — re-apply the extraction against dev's file
- Import `DraftMeta` (now w/ reset_ts/is_current), `build_cv2`, reset helpers, preview
  renderer, `_discord_error_note`, `render_post_html`, `_format_reset_ts`,
  `WeaponRef`, `iter_weapon_items`, `resolve_weapon`, `HybridPostSpec` from core; **keep
  every `wr.<name>` the 63 tests use** (re-export/`as`-alias the ones not used internally,
  as before — watch ruff stripping them).
- `post_or_edit_unpublished` / `publish_draft` → thin wrappers over `core` + `_SPEC` (core
  now stamps reset_ts). Delete WR's local `_send_new_post` (moved).
- Routes → thin wrappers: `_handle_create` = `core.post_action(_SPEC, payload, _bot,
  create=True)`, `_handle_edit` = `create=False`, plus `_handle_form_get/_preview/
  _delete/_auto` over the core fns; `register_weekly_reset_routes` uses `/create` + `/edit`.
  Move the `_post_action` body to core; WR keeps only the wrappers.
- `_build_bootstrap` includes `post_this_period` (via `meta.is_current`).
- Everything else stays local (`build_body`, `validate_post`, `_context_from_payload`,
  `apply_*`, `_build_indexes`/classifiers, constants).

## `trials.py` + form — adopt the Create/Edit model
- Routes → `/trials/{create,edit,preview,delete,auto}`; `_handle_create`/`_handle_edit`
  wrap `core.post_action(_SPEC, …, create=…)`; `_handle_form_get/_preview/_delete/_auto`
  over the core fns. `register_trials_routes` updated.
- `_build_bootstrap` sends `post_this_period = meta.is_current(current_reset_ts())` +
  `crossposted` (drop the old `posted`).
- `run_trials_draft` unchanged (still calls `core.post_or_edit_unpublished`, which now
  stamps reset_ts).
- **`trials_form.{js,html}`**: replace Save/Publish/Delete with Create / Create & publish
  / Edit / Edit & publish / Delete, mirroring dev's `weekly_reset_form.js` `postAction`
  helper + button-visibility logic; POST to `/trials/{create,edit,delete,auto}`; add the
  control-panel back-link (`b62e047`) for parity. Keep the focus-pool Tom Select + the
  `.md-h3`/`.md-bullet` preview CSS.

## Tests
- **`test_weekly_reset.py`**: dev's version (63 tests) is the source of truth — it must
  pass **unchanged** after re-extraction. Do NOT hand-edit it; make the code satisfy it.
- **`test_trials.py`**: update the route tests from `_handle_save`/`_handle_publish` to
  `_handle_create`/`_handle_edit` (± `publish`), assert 409 on create-when-current /
  edit-when-absent, `post_this_period` in responses, `reset_ts` stamped on post and cleared
  on delete, and `DraftMeta.is_current`. Keep the format/round-trip/validation/payload/
  preview tests.

## Verification
- `make check` (ruff + ty + pytest) green; dev's `test_weekly_reset.py` passes unchanged.
- Smoke: `form_get` renders + injects bootstrap (`post_this_period`); `post_action`
  create→409-on-second-create, edit→409-when-absent, publish path crossposts; delete
  clears `reset_ts`; homepage lists the Trials card; `/trials/*` routes register.

## Retained from the original plan (unchanged by dev)
- **Post format** (masked-link title, `### Featured Maps`, static Rewards, manifest-linked
  `**This Week's Bonus Focus Pool**`, `### Good luck…  :gscheer:`, image); `Live until` =
  `next_reset_ts(reset_ts)`.
- **Data model** `TrialsContext`/`TrialsConfig`; **`build_body`**; the **`_render_line`**
  H3/bullet extension (WR output byte-identical); the manifest focus-pool picker; central
  Discord-OAuth auth (`web_auth.py`) — producers carry no auth code.

## Future work — fixed-rotation focus pool (engineer later)
Owner note: the bonus focus pool weapons are on a **fixed rotation**. Encode it as a
**JSON** and auto-derive `TrialsContext.focus_pool` deterministically in
`build_draft_context` (like `weekly_reset`'s `compute_rotator`); until then the pool stays
human-entered (manifest-linked) in the form. Out of scope for this reconciliation.

## Open items for re-review
- Trials Create/Edit split confirmed above; flag if you'd rather Trials keep a simpler
  single Save/Publish (would mean the core route layer supports both shapes).
- Auto-suppress Trials autopost on Iron Banner weekends? (Deferred; seeds Friday, publish
  manual.)
