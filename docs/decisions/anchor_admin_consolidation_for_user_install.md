# Consolidate all admin commands into anchor, then user-install anchor for admins only

> **Status:** DEPRIORITIZED (2026-06-25) — evaluated in depth and shelved as not worth
> it *at this stage*. The friction it removes (two admins occasionally running admin
> commands outside the control guild) doesn't justify the engineering. Kept as a record;
> revisit only if that friction, the team size, or the admin surface grows. The design
> below stands if picked up. No code written yet. Re-verify every symbol and line
> reference below by grepping (names, not line numbers) before implementing — this repo
> changes under you.
>
> **Why deprioritized (the costing that killed it):** doing this properly means moving
> *all* admin onto anchor, including mirror's act-now commands (`mirror_send`/`update`/
> `cancel`/`delete_msg`). Mirror delivers via **beacon's own REST** (`channel.send` +
> `crosspost_message`, `mirror_core` kernels → `mirror.py` ~L649), so anchor — not being
> the bot in the destination guilds — can **never execute the sends**; execution is
> permanently beacon's. That forces a **low-latency inter-bot control plane** (an
> authenticated HTTP RPC on beacon over Railway private networking; anchor triggers +
> acks, beacon executes and keeps owning the live progress message + cancel button so the
> `run_till_completion` / `render_mirror_progress` / `kernel_work_control_registry`
> machinery doesn't move) **plus** a user_commands split (admin group → anchor; resync
> driven via that same control plane, backstopped by a slow reconcile). DB-poll signalling
> was rejected for this (can't be low-latency without hammering MySQL; no LISTEN/NOTIFY).
> The control-plane + split is real new surface for marginal payoff — not now.
>
> **Repo rules:** uv only; ruff (line length 88, double quotes, `E F W I UP B SIM`); ty;
> async throughout; tests live in each package's `tests/`. **NEVER deploy to prod by any
> means.**
>
> **Manual step the user owns (not automatable):** enabling **User Install** in each
> bot's Discord Developer Portal (Installation tab) and setting the install link to
> "None". Code cannot toggle this; a `USER_INSTALL` registration is rejected at sync
> until it's on.

## Why this plan exists

We investigated making the **control-guild admin commands user-installable** so the two
team members can run them from anywhere (DMs, any server). It's mechanically supported
(see "User-install mechanics" below) but was **rejected for now** on one ground:

> **Visibility leak.** Discord has *no per-user install allowlist*. Any user who installs
> the app sees the admin command names / descriptions / option schemas (execution stays
> blocked by `owner_only`, but the surface is visible). For **beacon** — a public bot —
> enabling user-install also risks its public global commands inheriting user-install via
> Discord's ambiguous `integration_types` default
> (see https://github.com/discord/discord-api-docs/issues/7108).

**The fix that removes the blocker:** move *all* admin commands onto **anchor**, leaving
anchor with **zero end-user surface**. Then:

- Beacon is purely public — no admin commands to accidentally expose, no `integration_types`
  default footgun to worry about.
- Anchor is purely admin. Its end users *are* the admins. With a private install link, in
  practice only admins ever install it, so "command visibility to end users" is a non-issue
  — there are no end users.

This plan is the **prerequisite refactor** (consolidation + inter-bot communication). The
user-install flip on anchor is the easy last step once anchor is self-sufficient.

## Current admin-command inventory (verify by symbol)

**Anchor — already entirely admin.** Whole client is gated `hooks=[owner_only]` and scoped
to the control guild via `default_enabled_guilds` (`dd/anchor/__main__.py`, the
`client_from_app(bot, utils.guild_scope(*cfg.test_env, cfg.control_discord_server_id), …)`
call). Extensions: `controller` (the `ddv1`/"kyber" group — restart/all_stop/info),
`bungie_api`, `eververse`, `gunsmith`, `xur`, `lost_sector`, `portal_ops`, `help`,
`source`, `posts`. `posts.py` and `lost_sector.py` additionally opt into the **Kyber**
guild because they *post into Kyber*.

**Beacon — admin commands to move to anchor.** Found via
`rg -n "control_discord_server_id" dd/beacon`:

| Command surface | File (verify by symbol) | What it does |
|---|---|---|
| `command` group (`lb.Group("command", …)`) | `dd/beacon/extensions/user_commands.py` (`command_group`, registered near `loader.command(command_group, guilds=guild_scope(*cfg.test_env, cfg.control_discord_server_id))`) | CRUD for DB-backed **custom/user commands** that are themselves registered as **public global commands on beacon**. |
| `mirror` group | `dd/beacon/extensions/mirror.py` (`mirror_group`) + sibling commands scoped to control+Kyber | Configure message mirroring (a beacon runtime feature). |
| stats group | `dd/beacon/extensions/statistics.py` (`stats_command_group`) | Read command-usage stats from the shared DB. |
| testing command | `dd/beacon/extensions/testing.py` | Beacon test helpers. |

## The hard part: inter-bot communication

Beacon's admin commands aren't pure DB editors — several **drive beacon's running
process**, which a command living in the anchor process cannot do directly. Per-command
analysis and the cross-process need:

- **`stats` group** — read-only over the shared DB (`dd/common/schemas.py`). **Trivial to
  move**: anchor reads the same tables. No signaling needed.

- **`command` group (user_commands)** — the crux. The custom commands it manages are
  registered on **beacon's live `lb.Client`** at runtime (`resync_user_commands(...)` →
  `client.register(...)` → `client.sync_application_commands()`; beacon injects its live
  client for this via `client.di...register_value(lb.Client, client)` in
  `dd/beacon/__main__.py`). If the management UI moves to anchor, anchor can: (a) write the
  `UserCommand` DB rows, but (b) **must signal beacon to re-register + resync** its
  application commands. Anchor has no handle to beacon's client.

- **`mirror` group** — mostly DB config (`MirroredChannel` etc.; see
  `dd/beacon/mirror_core.py`, `mirror reconciliation model` memory). Beacon runs the actual
  mirror listeners. Config edits beacon can pick up lazily, but any "act now" operation
  (e.g. kick off a backfill / `run_till_completion`) needs to signal beacon.

- **`testing` command** — assess case-by-case; may be beacon-process-specific and not worth
  moving (could stay, or be reworked).

**Communication mechanism — lean on the DB, not Redis.** Per the `redis-evaluated-and-rejected`
memory (no net win at this scale; use a DB config table for runtime config), do **not**
introduce Redis/pub-sub or a bespoke HTTP RPC. Options, cheapest first:

1. **Shared-DB state + beacon reacts.** For config-style changes (mirror add/remove), anchor
   writes rows; beacon reads them where it already does. Zero new machinery for these.
2. **DB-backed signal/outbox table** for "act now" operations (notably user_commands resync).
   Anchor inserts a signal row; beacon consumes it. Beacon needs a consumer — either a short
   poll loop (`@loader.task`, `max_failures=-1` per the `loader-task-max-failures-convention`
   memory) or react on its existing tick. Latency = poll interval; fine for admin actions.
3. **Discord control-channel message as a signal** (anchor posts in a control channel,
   beacon's message listener reacts). Works without new tables but is hacky and ties two
   bots to a channel; prefer (2) unless a table is unwanted.

Recommended: (1) for mirror config, (2) for user_commands resync. Keep the signal contract
tiny and explicit (one table, an enum action + payload + consumed flag).

## User-install mechanics (the easy last step — recap from investigation)

Once anchor is self-sufficient, flip it to user-install. Confirmed supported on the pinned
stack (hikari `2.5.0`, lightbulb `3.2.3`) — no upgrade needed:

- lightbulb commands (`lb.SlashCommand`/`UserCommand`/`MessageCommand`) and `lb.Group` accept
  `integration_types` + `contexts` kwargs; `as_command_builder` wires them into hikari's
  `set_integration_types`/`set_context_types` (`lightbulb/commands/commands.py`,
  `groups.py`). Enums: `hikari.ApplicationIntegrationType.{GUILD_INSTALL,USER_INSTALL}`,
  `hikari.ApplicationContextType.{GUILD,BOT_DM,PRIVATE_CHANNEL}`.
- **Hard Discord constraint:** `integration_types`/`contexts` *"only affect global
  commands"*. User-install is impossible for guild-scoped commands — so anchor must register
  **globally** (drop `default_enabled_guilds` → `cfg.test_env or ()`, mirroring beacon's
  `__main__.py`; empty default → global; keep test-guild registration in test-env for instant
  iteration since global propagation is ~slow/up to 1h).
- Use **`integration_types=[USER_INSTALL]` only** (not `GUILD_INSTALL`): the command then
  appears *only* for users who installed the app — not in any guild's list. Adding
  `GUILD_INSTALL` would spray admin commands into every server the bot is in.
- `contexts=[GUILD, BOT_DM, PRIVATE_CHANNEL]` for "works anywhere".
- **Auth unchanged:** keep `owner_only` (gates on the Discord **Team**,
  `dd/common/auth.py` + `bot.py` `fetch_owner_ids`). It runs in the CHECKS step regardless
  of invocation context and bypasses Discord's `default_member_permissions` (which doesn't
  apply in DM/user-install anyway).
- Suggested DRY: a `dd/common` constant pair (`ADMIN_INTEGRATION_TYPES`, `ADMIN_CONTEXTS`)
  passed to each command/group, rather than per-call literals.

**Edge case:** a user-install command invoked in a guild the bot isn't in can only reply
ephemerally (no guild state, no public post). Anchor commands that just respond to the
caller are fine; anything that posts into a specific channel (posts/lost_sector → Kyber)
needs the bot present there — decide whether those stay guild-install or post via REST.

## Suggested order

1. Move `stats` to anchor (read-only; proves shared-DB access end-to-end).
2. Build the DB-backed signal contract; move `command` (user_commands) — anchor edits rows
   + signals, beacon consumes signal and resyncs its public commands.
3. Move `mirror` admin surface (config via shared DB; signal for act-now ops).
4. Decide on `testing` (move, rework, or leave).
5. Confirm anchor has **zero** non-admin surface; flip anchor to global `USER_INSTALL`
   (Dev Portal toggle + private install link; `owner_only` retained).

## Open questions / decisions for implementer

- **user_commands ownership:** the custom commands are *public* and must live on beacon.
  Only the *management* moves to anchor; confirm the resync-signal design before coding.
- **mirror "act now" ops:** enumerate which mirror admin actions need immediate beacon
  action vs. lazy pickup; only the former need a signal.
- **Anchor's Kyber-posting commands** (`posts`, `lost_sector`): keep guild-install, or make
  user-install and post via REST? (Bot must be in Kyber either way.)
- **Relationship to `plans/shared_controller_extension.md`:** that plan proposes *sharing*
  control commands onto **both** bots and renaming `ddv1`. That's in mild tension with
  "all admin lives only on anchor" — reconcile the two before implementing either (this
  plan argues for anchor-only admin; the shared-controller plan argued for both). The
  rename portion is orthogonal and still applies.
