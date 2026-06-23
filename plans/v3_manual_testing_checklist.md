# Review & Manual Testing TODO

> **Branch-scoped, transient:** this checklist tracks manual testing for the in-flight
> `feature-lightbulb-v3` work. **Delete this file when the branch merges** (or fold it
> into the PR description).

Remaining checklist for the logging/alerting, DI, and extension-loader work on
`feature-lightbulb-v3`. Tick items as you go. (Completed items cleared.)

- [x] **Loud loader for both bots** — an extension import error is logged at CRITICAL
  with a traceback and the broken module is skipped; the remaining extensions load
  and startup continues (does **not** abort) (`dd/common/extension_loader.py`).
- [x] **Disabled-count escalation** — with `DISABLE_BAD_CHANNELS=true` and enough
  failing channels, confirm severity steps (escalation keys on *disabled* channels,
  i.e. `legacy_error_rate >= 3`, not single-run failures):
  - [x] WARNING below thresholds
  - [x] ERROR past `> 5% or > 5` disabled
  - [x] CRITICAL past `> 10% or > 10` disabled

## 4. Announcer offline behavior (anchor)

- [x] Simulate Bungie API offline (block host or stub `check_bungie_api_online`):
  - [x] First failure logs with traceback; subsequent retries go quiet (debug)
  - [x] Backoff grows (not pinned at 2s)
  - [x] After `ANNOUNCER_OFFLINE_ALERT_AFTER`, a **single** CRITICAL fires
- [x] Recovery — once the API is back, the autopost resumes and posts.

## 5. DI-converted commands (exercise each at least once)

Each should respond, not error.

- [x] **Anchor**
  - [x] `/post create`
  - [x] message command `edit`
  - [x] message command `copy`
  - [x] `ddv1 all_stop` (bot shuts down → restarts)
  - [x] `ls_update` (message command)
  - [x] `/help`
  - [x] autopost `auto` / `send` / `show` subcommands
- [x] **Beacon**
  - [x] `/help`
  - [x] `/stats populations`, `/stats server_list`
  - [x] `mirror manual_add` / `source_details` — `source_details` confirmed live;
    `manual_add` verified-by-integration (its legacy rows drove the escalation run,
    and `source_details` reads them back). Both now accept channel link/mention/id so
    cross-server dests work.
  - [x] message commands `mirror_send` / `mirror_update` / `mirror_cancel`
  - [x] `command` preview (custom-command system)
  - [x] **`command` owner-gate hook + shared error handler (new)** — the five
    `/command` subcommands now gate on a single `_owner_only` CHECKS hook and report
    failures via one `loader.error_handler`, replacing the per-command gate + try/except:
    - [x] Non-owner invoking any of preview/add/delete/edit/rename gets the ephemeral
      "You are not the owner of this bot." and the command does **not** run
    - [x] Owner can still run all five subcommands successfully
    - [x] A deliberate error (e.g. `command edit` on a non-existent command) renders the
      shared error embed (FriendlyValueError message + traceback), not a silent failure
    - [x] Errors from **other** commands/extensions are unaffected (the handler returns
      `False` outside the `command` group)
  - [x] `autopost` enable/disable
- [x] **Scheduler listeners** — confirm at least one scheduled autopost fires
  (xur / gunsmith / eververse / lost_sector); these now get `bot` via DI in the
  `StartedEvent` listener.

## 6. Extension loader

- [x] **Negative test** — temporarily add `import nonexistent_xyz` to one extension:
  - [x] A CRITICAL log names that module; the bot still starts with that extension
    skipped (the rest load) — startup is not aborted
  - [x] **Revert** the edit afterwards

## 7. Regression smoke

- [x] `free_games` listeners — message create/update/delete still refresh the cached
  message (converted listeners). **Bug found & fixed:** `on_message_create`/`on_message_update`
  stored `event.channel_id` instead of `event.message.id` as the tracked message id, so
  update/delete (which gate on message-id match) were silently ignored.
