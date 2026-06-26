# Make the bot-control commands a shared extension + rename them

> **Status:** planning / not started. No code written yet. Re-verify every symbol and
> line reference below by grepping (names, not line numbers) before implementing — this
> repo changes under you.
>
> **Repo rules:** uv only; ruff (line length 88, double quotes, `E F W I UP B SIM`); ty;
> async throughout; tests live in each package's `tests/`. **NEVER deploy to prod by any
> means.** Changing the prod restart policy (below) is a manual dashboard action for the
> user, not something this plan automates.

## Goal

Today the bot-control commands live only on **anchor**, in
`dd/anchor/extensions/controller.py`, as the group `ddv1` ("Commands for Kyber") with
subcommands `all_stop`, `restart`, `info`. Two changes wanted:

1. **Share them** so *both* bots (beacon + anchor) expose control commands, following the
   established factory pattern (`dd/common/source.py` → `make_source_command()`, wrapped by
   the thin per-bot `dd/{beacon,anchor}/extensions/source.py`).
2. **Rename** off the cryptic legacy `ddv1` / "Kyber" naming.

There is **no pre-existing plan** for this — only references are `controller.py` itself and
`docs/v2_v3_behavior_audit.md` (which records `anchor/controller (pair) — no divergence`
from the v2→v3 audit; that doc will need updating, see below).

## Current definition (verify by symbol)

`dd/anchor/extensions/controller.py`:

- `kyber = lb.Group("ddv1", "Commands for Kyber")`
- `AllStop` (`name="all_stop"`) → `await ctx.respond(...)`; `await bot.close()` → clean
  process **exit 0**.
- `Restart` (`name="restart"`) → `ctx.respond(...)`; `sys.exit(1)` → **non-zero exit**,
  relies on Railway respawning the container.
- `Info` (`name="info"`) → dumps `cfg.control_discord_server_id`, `cfg.test_env`, the
  lost_sector + xur followable channels.
- Registered via `loader.command(kyber)` with **no `guilds=`** → inherits anchor's
  client default-enabled-guilds, and **no per-command gate** — it leans entirely on
  anchor's client-wide `hooks=[owner_only]`. The file comment says so explicitly.

## Caveat 1 — restart policies DIVERGE (confirmed against Railway, 2026-06-24)

The two commands depend on Railway's restart policy, and the policy is **not** uniform:

| environment | beacon | anchor | MySQL |
|---|---|---|---|
| **production** | **`ALWAYS`** (maxRetries unlimited) | `ON_FAILURE` (7) | `ON_FAILURE` (10) |
| **dev** | `ON_FAILURE` (10) | `ON_FAILURE` (7) | `ON_FAILURE` (10) |

The exit-code contract these commands rely on:

- `restart` = `sys.exit(1)` (non-zero) → needs *restart-on-failure* → works under **both**
  `ALWAYS` and `ON_FAILURE`. ✅ Fine on every service/env.
- `all_stop` = `bot.close()` → clean **exit 0** → needs Railway to **not** restart on a
  clean exit → works under `ON_FAILURE` / `NEVER`, but **NOT under `ALWAYS`**.

**Consequence:** a shared `all_stop` shipped to beacon would behave correctly on **dev
beacon** and **both anchors**, but on **production beacon** the bot would **respawn right
after "shutting down"** — `all_stop` silently fails to stop the prod main bot. (`restart`
is fine everywhere.) This is why prod beacon being `ALWAYS` matters: anchor has always been
`ON_FAILURE`, which is the implicit contract `all_stop` was written against.

### Decision needed before shipping `all_stop` to beacon

- **(A) Recommended — make prod beacon `ON_FAILURE`.** A manual change in the Railway
  dashboard (beacon service → production → Settings → Restart Policy → On Failure). Makes
  all four service-instances uniform and gives beacon a real `all_stop`. Why is prod beacon
  `ALWAYS` today? Probably a deliberate "main bot should always be up" choice made when
  beacon had no `all_stop` to conflict with — confirm with the user that flipping it is
  acceptable. (maxRetries differing — anchor 7 vs beacon 10 — is cosmetic; ignore.)
- **(B) Keep prod beacon `ALWAYS`** and either omit `all_stop` from beacon's wrapper, or
  document that on beacon `all_stop` degrades to "clean restart, not a true stop." Less
  surprising to leave it off than to ship a command that lies.

Project/service/env IDs for re-verification (`railway status --json`, project `kyber`):
beacon `9f5deb0d-f430-4f44-8bc6-77f03ec91e58`, anchor
`1b5d3ecd-a74f-42ac-85fe-f5dfd4306a4a`; prod env `67172498-…`, dev env `c275134c-…`.

## Caveat 2 — owner-gate / guild-scope DIVERGE between the clients

The current `controller.py` gates nothing itself; it relies on anchor's setup. Beacon's
client is configured differently, so a naive copy would be **dangerous**:

- **anchor** (`dd/anchor/__main__.py`): `client_from_app(bot, guild_scope(*cfg.test_env,
  cfg.control_discord_server_id), hooks=[owner_only])` — client-wide owner gate **and**
  control-guild default scope. Controller commands are covered automatically.
- **beacon** (`dd/beacon/__main__.py`): `client_from_app(bot, cfg.test_env or (),
  hooks=[track_command_usage])` — **no client-wide owner gate**, and the default scope is
  *global* outside a test env. Beacon's own admin commands therefore each carry their own
  `hooks=[owner_only]` **and** `guilds=guild_scope(*cfg.test_env,
  cfg.control_discord_server_id)` (see `dd/beacon/extensions/user_commands.py`, the
  `command` group registration near the bottom).

**Therefore the shared factory MUST apply both an `owner_only` hook and the control-guild
scope itself**, rather than inheriting them. Otherwise "restart / shut down the *main*
bot" would be globally registered and ungated on beacon. Applying them is redundant-but-
harmless on anchor (double owner check is idempotent; the explicit scope matches its
default), so the factory can apply them unconditionally for both bots.

## Naming

Both bots register into the **same control guild**, so a *shared* group name (`/admin`,
`/bot`, `/ops`) would show the owner two identical `/admin restart` entries — one routing
to beacon, one to anchor — indistinguishable in the picker, exactly where ambiguity is most
dangerous. (Discord allows it: command names are unique per *application*, not per guild.)

**Recommended — name the group after the bot** so it's self-evident which bot you're
commanding:

- beacon → `/beacon restart`, `/beacon stop`, `/beacon info`
- anchor → `/anchor restart`, `/anchor stop`, `/anchor info`

Subcommands:

- `all_stop` → **`stop`** (or `shutdown` if maximally explicit is preferred). Pairs cleanly
  with `restart`; drops the cute nautical "all stop".
- `restart` → **keep** (already clear).
- `info` → **keep** (or `config`).

Group description: replace "Commands for Kyber" with e.g. **"Bot administration"**.

*Alternative if a single shared name is preferred anyway:* `/ops` or `/admin`, accepting the
two-identical-entries ambiguity, or adding a required `bot:` choice arg (more machinery,
little gain). Not recommended.

## Implementation outline

1. **Add `dd/common/controller.py`** with a factory, mirroring `make_source_command()`:
   `make_controller_group(bot_name: str) -> lb.Group`. It builds a fresh `lb.Group(bot_name,
   "Bot administration")` each call (do **not** share a single Group/command instance across
   two clients — lightbulb command objects carry registration state, same reason
   `make_source_command` returns a fresh class), registers `restart` / `stop` / `info`
   subcommands on it, and applies the `owner_only` hook (verify the exact lb v3 group-level
   hooks API by symbol — see how `user_commands.py` attaches `owner_only`).
   - `restart`: `ctx.respond(...)`; `sys.exit(1)` (unchanged behaviour).
   - `stop`: `ctx.respond(...)`; `await bot.close()`. Needs the `CachedFetchBot` injection
     (`bot: CachedFetchBot = lb.di.INJECTED`) exactly as the current `AllStop` does — both
     bots register that injectable in their `__main__`, so it resolves on each.
   - `info`: same `cfg`-based dump as today.
2. **Thin per-bot wrappers** `dd/beacon/extensions/controller.py` and
   `dd/anchor/extensions/controller.py`:
   ```python
   loader = lb.Loader()
   loader.command(
       make_controller_group(<"beacon"|"anchor">),
       guilds=guild_scope(*cfg.test_env, cfg.control_discord_server_id),
   )
   ```
   `guild_scope` is `dd/common/utils.py:guild_scope` (strips guild-id 0 so the list never
   collapses to a global registration). Both bots auto-discover `extensions/controller.py`
   via `load_extensions_strict(client, <pkg>)`, so no `__main__` changes needed.
3. **Delete** the old `ddv1`/`AllStop`/`Restart`/`Info` definitions from the anchor file
   (replaced by the wrapper above).
4. **Resolve Caveat 1** per the user's decision: either flip prod beacon to `ON_FAILURE`
   (manual, by the user) before/with shipping `stop` to beacon, or omit `stop` from the
   beacon wrapper.
5. **Update `docs/v2_v3_behavior_audit.md`** — the `anchor/controller` row (and the
   "anchor/controller (pair) — no divergence" line) now describe a shared, renamed,
   self-gated extension on both bots.
6. **Smoke-check** in dev only (`make deploy-*-dev`): `/beacon info` + `/anchor info`
   render; `/…/restart` respawns; `/…/stop` on dev beacon (now `ON_FAILURE`) stays down.
   Never touch prod via deploy.

## Open decisions for the user

- Caveat 1: flip prod beacon to `ON_FAILURE` (option A) or keep `ALWAYS` and drop/soften
  beacon's `stop` (option B)?
- Confirm the names: per-bot group (`/beacon …`, `/anchor …`) and `all_stop` → `stop`
  (vs `shutdown`)?
