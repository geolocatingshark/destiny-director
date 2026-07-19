# Website interface for user-defined commands

## Status: fully scoped, ready to build (owner decisions locked 2026-07-19)

> **Depends on two builder plans, authored first:**
> - `plans/web_embed_builder.md` — classic `h.Embed` builder (this plan's `response_type 3`).
> - `plans/web_cv2_builder.md` — Components-V2 builder (this plan's **new** `response_type 4`).
>
> Those two own the embed/CV2 authoring widgets, serialization, and safe-HTML preview render.
> This plan owns the command manager itself: the DB/CRUD wiring, the cross-process resync
> signal, the anchor web pages that mount the builders, and the deprecation of the in-Discord
> authoring surface.

## Context — why this exists

User-defined `/command`s (`dd/beacon/extensions/user_commands.py` + `UserCommand` in
`dd/common/schemas.py`) are authored today **only** in Discord via an owner-only `/command
add|edit|delete|rename` group — no preview, clumsy delete+re-add editing, and rich formats
(embeds) hidden behind `EMBEDS_FEATURE_FLAG` because they can't be previewed safely. Every other
post surface already has a web UI with a live, safe-HTML Discord preview.

**Goal:** a web command manager in the anchor bot — list every command, create/edit/delete with
response-type-specific fields and a live preview, mounting the two reusable builders for the
rich formats — and make the web UI the eventual **sole** authoring surface.

## Owner decisions locked in (2026-07-19)

- **Structured builders, not raw JSON:** embeds via `web_embed_builder`, CV2 via
  `web_cv2_builder`.
- **CV2 is a new response type** (`4` = Components-V2), stored as node JSON, alongside embeds.
- **Remove `EMBEDS_FEATURE_FLAG` entirely** — delete the constant and the in-Discord code paths
  that only run when it is `True`.
- **Cross-process resync = DB version-signal + beacon poller** (dedicated `user_command_sync`
  table + one Atlas migration); accepts relaxing today's in-transaction rollback-on-sync-failure
  for the web path (failures become visible + self-healing).
- **Deprecate the in-Discord `/command` authoring** in a later follow-up; the web UI ships now.

## Response types (after this work)

`0` group · `1` plain text · `2` message-copy · `3` embed *(via `web_embed_builder`)* ·
**`4` Components-V2 *(via `web_cv2_builder`)* — new**. Types 3 and 4 store their canonical JSON
(embed kwargs / node list) in `UserCommand.response_data`. The beacon gains a **type-4 runtime
handler** that sends the stored node JSON via `cv2_raw.RawComponentBuilder`; the **type-3
runtime** is extended (in `web_embed_builder`) to render the full embed.

## Architecture constraints (verified)

- **Two processes, one DB.** Commands live in **beacon**; the web UI lives in **anchor** (only
  anchor binds `cfg.port`). MySQL (no LISTEN/NOTIFY); beacon has no inbound HTTP. So anchor can
  write the shared `user_command` table but cannot call `resync_user_commands` (needs the live
  beacon `lb.Client`). Stage A solves this.
- **`user_command`** (`schemas.py:1768`): `id`, `l1/l2/l3_name` (`""` unused), `description`,
  `response_type`, `response_data`; natural key = the (l1,l2,l3) tuple. CRUD classmethods
  (`fetch_commands`, `fetch_command_groups`, `fetch_command`, `fetch_subcommands`, `add_command`,
  `add_command_group`, `delete_command`, `delete_command_group`, `check_parent_command_groups_
  exist`) enforce validation via a regex `@validates` hook → `FriendlyValueError` + unique/check
  constraints. **No in-place UPDATE** — edit = delete-then-add in one `session.begin()` (mirror
  `EditCommand.invoke`, `user_commands.py:718`).
- **Reuse (no reinvention):** anchor web extensions auto-register routes/cards via
  `web.register_routes` / `web.register_card`; `web_auth._auth_middleware` already gates
  **everything** to bot-owners + Origin-CSRF (zero auth code in handlers); no template engine —
  HTML files in `web_static/` with a `"/*__BOOTSTRAP__*/ null"` `str.replace`. The
  **`rotation_editor.py`** list+editor pattern is the template (GET home / GET+POST edit / POST
  preview / GET search-json) — not the single-post hybrid lifecycle. The beacon
  `_refresh_emoji_loop` idiom (`dd/common/bot.py:175`) is the model for the poll loop.

## Stage A — cross-process resync signal  ·  PR `feat/user-command-sync-signal`

Ship **before** the web UI so the trigger exists; the in-Discord path exercises it end-to-end
with no web surface yet. (Slash-command *registration* is what resyncs; this is independent of
the new response types, which only matter at invoke-time rendering.)

- **New singleton table** `UserCommandSync` (`dd/common/schemas.py`), modeled on
  `mirror_delivery`'s desired/applied-version pattern: `id` PK=1, `desired_version:int`,
  `applied_version:int`, `last_error: Text|None`, `updated_at`. Classmethods (all
  `@classmethod @ensure_session(db_session)`, session-injectable): `bump_desired`,
  `read → (desired, applied, last_error)`, `mark_applied(version)` (clears error),
  `mark_failed(version, error)` (leaves `applied` behind so the poller retries). Model the
  upsert on `AutoPostSettings.set_enabled`.
- **Migration:** `make atlas-migration-plan` → hand-check the generated file under
  `migrations/` → `make atlas-migration-dry-run` against MySQL. Commit it.
- **Beacon poller** (`dd/beacon/extensions/user_commands.py`): a self-scheduled `asyncio` loop
  started on `h.StartedEvent` (copy `_refresh_emoji_loop`); hold a strong task ref; cancel on
  `StoppingEvent` alongside `mirror_worker.stop()` in `dd/beacon/__main__.py`. Each tick:
  `read()`; if `desired != applied`, in one `session.begin()` call `resync_user_commands(client,
  session=session, sync=True)` then `mark_applied(desired)`; on exception log + `mark_failed`.
  New `cfg.user_command_poll_interval` (~10s — command edits are interactive; in `test_env`
  guild-scoped registration is instant so the poll interval is the whole visible latency).
- **Atomicity:** the in-Discord `/command` handlers keep resyncing **inside** their write
  transaction; make `resync_user_commands` (or its callers) also `bump_desired` + `mark_applied`
  in that same session, so a sync failure still rolls the whole txn back (today's guarantee
  intact) and the poller sees `desired == applied` (no double-sync). The **web path** relaxes
  this: anchor commits write + `bump_desired`, the poller converges with retry, `last_error`
  records failures, `/commands/sync-status` shows pending/synced/failed.

## Stage B — anchor web command manager  ·  PR `feat/command-manager-web-ui`

New auto-discovered extension `dd/anchor/extensions/user_commands_web.py` + templates. Follows
the `rotation_editor` list+editor shape. **Depends on Stage A** (trigger) and on both builder
plans (rich-format widgets + preview renderers).

- **Routes** (via `web.register_routes`, auto-gated by `web_auth`) + a `web.Card("Command
  Manager", "Create and edit custom /commands", "/commands")`; stash the live `_bot` on
  `h.StartedEvent` (rotation_editor idiom) for emoji + type-2 fetches:

  | Method / path | Purpose |
  |---|---|
  | `GET  /commands` | List page: command tree by layers, response-type badges, live sync-status badge, New-command/New-group actions. |
  | `GET  /commands/edit?id=` / `GET /commands/new` | Editor page (bootstrap-injected row or blank scaffold; `?parent=l1/l2` for a new child). |
  | `POST /commands/preview` | Dispatch by type → safe HTML (drives the preview box). |
  | `POST /commands/save` | Create-or-edit: validate → delete+add in one txn → `bump_desired`. |
  | `POST /commands/delete` | Delete command or group (cascade flag). |
  | `GET  /commands/sync-status` | `{desired, applied, pending, last_error}` banner. |
  | `POST /commands/resync` *(optional)* | Operator "force resync now" = `bump_desired`. |

- **List page** `web_static/commands_home.html` — server-render the tree from
  `fetch_command_groups()` + `fetch_commands()` (already ordered parents-before-children) via the
  `<!--__…__-->` placeholder swap.
- **Editor** `web_static/commands_editor.html` + `commands_editor.js` — bootstrap-inject the row
  (`"/*__BOOTSTRAP__*/ null"` swap, `<`-escaped). The `type` selector toggles field groups:
  - **Group (0):** layers + description; no preview.
  - **Plain text (1):** description + `response_data` textarea; live preview → `PostSpec.cv2(body,
    buttons=[("See more on Kyber's Corner!", cfg.default_url)])`.
  - **Message copy (2):** message-link input. **Preview deferred to an on-demand "Fetch preview"
    button** (never on keystroke): route parses the link (`parse_message_link`), fetches once,
    maps to a `PostSpec`/nodes, renders; graceful fallback to an info card on failure.
  - **Embed (3):** mounts **`initEmbedBuilder`** (`web_embed_builder`); live preview →
    `render_embed_html`.
  - **Components-V2 (4):** mounts the **CV2 builder widget** (`web_cv2_builder`); live preview →
    `render_cv2_nodes_html` (after `sanitize_for_preview`).
- **`/commands/preview` dispatch:** embed → `render_embed_html`; cv2 → `render_cv2_nodes_html`;
  text → `PostSpec.cv2`; message-copy → on-demand fetch. Returns safe HTML for `innerHTML`.
- **Save + edit-as-delete+add**, reusing the `UserCommand` classmethods + `FriendlyValueError`
  exactly as `EditCommand.invoke`:
  `async with db_session() as s, s.begin(): [delete_command(...)]; add_command(..., session=s);
  UserCommandSync.bump_desired(session=s)`. Catch `FriendlyValueError` → 400 `{"error":…}` (the
  JSON shape the client expects). Note: anchor **cannot** run `_warn_if_code_defined` (no live
  client) — a code-defined-name clash is caught by the beacon resync **self-heal** (auto-deletes
  the row, logs CRITICAL) and surfaced via `sync-status` `last_error`. This is exactly the "web
  UI as recovery path" note in `plans/legacy_rotation_cleanups.md` item 6.

## Stage C — remove `EMBEDS_FEATURE_FLAG` + Discord deprecation

- In the Stage B PR: delete `EMBEDS_FEATURE_FLAG` (`user_commands.py:54`) and the `if …:`-gated
  Discord embed-authoring paths (the "Embed" choice in `_type_choices` etc.). Rich formats are
  authored only via the web UI; the type-3/type-4 render runtimes stay.
- **Follow-up PR** `chore/remove-discord-command-authoring`: once the web UI is prod-confirmed,
  remove the in-Discord `/command add|edit|delete|rename` group, leaving the web UI as the sole
  authoring surface. The sync signal + poller (Stage A) already make this safe.

## Build order & PR split (dependency-aware)

1. `feat/web-embed-builder` — `plans/web_embed_builder.md`. Ships alone.
2. `feat/web-cv2-builder` — `plans/web_cv2_builder.md`. Ships alone.
3. `feat/user-command-sync-signal` — Stage A. Independent; parallel with 1–2.
4. `feat/command-manager-web-ui` — Stage B + C. Depends on 1, 2, 3.
5. `chore/remove-discord-command-authoring` — follow-up once prod-confirmed.

## Files (this plan; the builders own their own — see their plans)

- **Create:** `dd/anchor/extensions/user_commands_web.py`, `web_static/commands_home.html`,
  `commands_editor.html`, `commands_editor.js`, `dd/anchor/tests/test_user_commands_web.py`,
  `dd/beacon/tests/test_user_command_sync.py`, one migration under `migrations/`.
- **Modify:** `dd/common/schemas.py` (`UserCommandSync`), `dd/common/cfg.py`
  (`user_command_poll_interval`), `dd/beacon/extensions/user_commands.py` (type-4 handler,
  poller, in-txn `bump_desired`/`mark_applied`, remove `EMBEDS_FEATURE_FLAG` paths),
  `dd/beacon/__main__.py` (cancel poller on `StoppingEvent`).

## Verification

`make check` per PR; `make atlas-migration-dry-run` for Stage A. **E2E (local):** web dev-auth
bypass, `make anchor`, open `/commands`; author each response type (embed via structured fields,
CV2 via the node tree, previews matching Discord; type-2 via the on-demand button; friendly 400s
on bad input). Run `make beacon` in parallel (shared DB, `test_env` guild-scoped): after a web
save, confirm within `user_command_poll_interval` the beacon resyncs + `sync_application_
commands`, `applied_version` catches `desired_version`, the slash command appears/updates, and
`/commands/sync-status` flips pending→synced. Fault-inject a code-defined-name clash → confirm
beacon self-heal deletes the row and `last_error` surfaces it.

## Risks

- **XSS surface** (owned by the builder plans): all embed/CV2 fields through the whitelist +
  `html.escape` + http(s) validation; hex-validated accent.
- **Poller mutates the DB** (resync self-heal deletes clashing rows) — its `mark_applied` must
  commit in the same txn as any self-heal delete.
- Defaults taken: message-copy preview on-demand only; poll interval ~10s in cfg; optional
  `/commands/resync` force button.
