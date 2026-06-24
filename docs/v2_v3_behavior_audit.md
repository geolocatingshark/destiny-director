# v2 → v3 rewrite behavior audit

**What this is.** A systematic audit for behaviors that silently changed during the
hikari-lightbulb **v2 → v3** rewrite (old `dd/beacon/modules/*` + `dd/anchor/*.py` →
new `dd/beacon/extensions/*` + `dd/anchor/extensions/*`). Prompted by two regressions
already found and fixed in `dd/beacon/extensions/user_commands.py` (dynamic-command
`cfg.test_env` scope, and v3 autocomplete `ctx.respond`).

**Method.** Each of 39 module units was read in full on both sides and compared
semantically (commands/scope, schedules, listeners, permissions, flags, formatting,
wiring) — not by raw diff. Baseline = v2 sibling checkout `/home/gavin/destiny-director`
vs the current v3 working tree (which already contains the two fixes, so they do not
re-appear). Produced by a 41-agent fan-out workflow; coverage verified 39/39.

> **Provenance caveat (read before acting on xur/anchor content findings).** The v2
> baseline is the *sibling repo's current HEAD* — `be5a66a "Add legendary armor sets to
> Xur and fix hawkmoon perks"` — which contains commits made **after** the v3 rewrite
> forked. So several `anchor/xur` "divergences" (Legendary Armor Sets section, Hawkmoon
> `include_perks` `[2]`→`[1]`) are **v2 content never forward-ported into v3**, not
> behaviors the rewrite dropped. Treat those as "port forward?" decisions. The v3 tree
> separately *added* a Xurfboard / "Other Strange Offers" section.

---

## Executive summary

This audit compared 38 modules across the `beacon` and `anchor` bots for hikari-lightbulb v2→v3 behavioral divergences. Findings break down as follows.

**By classification:** 48 intentional, 19 needs-judgment, 1 regression.

**By severity:** 1 high, 9 medium, 58 low.

**Top regressions (action needed):**

- `anchor/bungie_api` — `bungie account_numbers` lost `auto_defer=True`, so the network-heavy invoke (token refresh + Bungie API calls) now runs before the first `ctx.respond`, risking the 3s interaction-ack timeout.

(That is the only finding classified `regression`. The single `high`-severity item — the dropped "Legendary Armor Sets" section in the Xur post — was classified `needs-judgment`, not a regression, because the Xur post content was deliberately reworked; see below.)

## Regressions (action needed)

| Module | Category | Item | v2 → v3 | Severity | Evidence |
|---|---|---|---|---|---|
| anchor/bungie_api | flags | `account_numbers` lost `auto_defer` | `auto_defer=True` (interaction deferred before network work) → no defer; `refresh_api_tokens`/`DestinyMembership.from_api`/`get_character_id` run before first `ctx.respond`, risking 3s ack timeout | medium | v2 bungie_api.py:1094 vs v3 extensions/bungie_api.py:1107-1133 |

## Needs judgment

| Module | Category | Item | v2 → v3 | Severity | Evidence |
|---|---|---|---|---|---|
| anchor/xur | lost-feature | Legendary Armor Sets section in Xur post | `legendary_armor_sets_fragment()` defined and appended (`## Legendary Armor Sets` listing) → function removed entirely; section no longer posted | high | v2 dd/anchor/xur.py:320-339,405-408 vs v3 dd/anchor/extensions/xur.py:393-396 |
| beacon/autoposts | wiring | Whole-invoke `session.begin()` transaction vs per-call `ensure_session` | `@ensure_session(db_session)` injected; mirror helpers commit internally → invoke wraps entire body in `async with db_session() as session: async with session.begin():`, all mirror mutations one atomic transaction | medium | v2 modules/autoposts.py:181-187 vs v3 extensions/autoposts.py:208-209 |
| beacon/help | commands | `/help <command>` / `/help <group>` targeted help | `DefaultHelpCommand` rendered per-command/per-group help via `send_command_help`/`send_group_help` → `/help` is a single SlashCommand with no argument; targeted help gone | medium | v2 dd/beacon/help.py:213-261 vs v3 dd/common/help.py:262-275 |
| beacon/help | lost-feature | Per-command extended help text | `build_help_lines` appended `cmd.get_help(...)` long help under each command → only `/<name> - <description>` rendered; long help never shown | medium | v2 dd/beacon/help.py:132-134 vs v3 dd/common/help.py:107-131 |
| beacon/mirror | commands | `mirror delete_msg` lost kyber-guild availability | own `guilds=[control, kyber]` (both servers) → inherits group `guilds=[*test_env, control]`; kyber dropped | medium | v2 mirror.py:1145-1154 vs v3 mirror.py:1194-1199,1338 |
| beacon/statistics | permissions | `stats` group scope global → control-guild-only | `bot.command(stats_command_group)` global (hidden=True kept it out of UI) → `loader.command(..., guilds=[control])`; visible/invocable only in control server | medium | v2 modules/statistics.py:168-169 vs v3 extensions/statistics.py:176 |
| anchor/lost_sector | commands | `ls_update` scope global → control-guild | `bot.command(ls_update)` no guilds (global) → `loader.command(LsUpdate, guilds=[control])`; control guild only | medium | v2 dd/anchor/lost_sector.py:103,155 vs v3 extensions/lost_sector.py:110-114,170 |
| anchor/xur | formatting | Hawkmoon exotic perk index | `include_perks=[2]` → `include_perks=[1]` | medium | v2 dd/anchor/xur.py:267 vs v3 extensions/xur.py:256 |
| anchor/xur | formatting | Xurfboard / Other Strange Offers section added | n/a → `xurfboard_sparrow_fragment()` posts hardcoded `## Other Strange Offers` (The Xurfboard, Cost x97 strange coins) | medium | v2 absent vs v3 dd/anchor/extensions/xur.py:337-347,397 |
| anchor/xur | permissions | Admin gate on `/xur default_image` | no invoker check → `if not await utils.check_admin(ctx): return` | medium | v2 dd/anchor/xur.py:568-584 vs v3 extensions/xur.py:600-601 |
| anchor/embeds | formatting | `edit_image` empty URL handling | no guard; `""` → `follow_link_single_step("")` → `set_image("")` → `if not image_url: return`; empty field leaves image unchanged (can no longer clear) | medium | v2 dd/anchor/embeds.py:216-223 vs v3 embeds.py:210-222 |
| anchor/embeds | formatting | `edit_thumbnail` empty URL handling | no guard; `""` → `set_thumbnail("")` → `if not thumbnail_url: return`; empty field leaves thumbnail unchanged (can no longer clear) | medium | v2 dd/anchor/embeds.py:230-237 vs v3 embeds.py:229-241 |
| beacon/autoposts | formatting | Autopost group long help text dropped/folded | `lb.set_help(...)` ping-role guidance shown via help → no `set_help`; condensed into one-line group description | low | v2 modules/autoposts.py:59-63 vs v3 extensions/autoposts.py:38-41 |
| beacon/help | formatting | Single-subcommand group placement | `command_group_size <= 1` folded into General category → every top-level `lb.Group` gets its own category | low | v2 dd/beacon/help.py:85-101,127 vs v3 dd/common/help.py:134-164 |
| beacon/source | formatting | AGPL notice body newline handling | no trailing backslashes (hard newlines preserved per line) → shared template adds trailing `\` continuations, collapsing paragraphs into soft-wrapped lines | low | v2 dd/beacon/modules/source.py:25-34 vs v3 dd/common/source.py:27-36 |
| beacon/user_commands | commands | AddCommand option ordering | response, type, description, layer1/2/3 → type, description, layer1/2/3, response (layers between description and response) | low | v2 modules/user_commands.py:232-247 vs v3 extensions/user_commands.py:582-591 |
| beacon/user_commands | wiring | Type-2 (Message Copy) fetch source | `ctx.bot.rest.fetch_message` (raw REST, uncached) → `bot.fetch_message` on CachedFetchBot (cache-aware) | low | v2 dd/beacon/bot.py:329-331 vs v3 extensions/user_commands.py:198-201 |
| beacon/user_commands | formatting | Type-3 (Embed) image pop on shared dict | `embed_kwargs.pop("image")` mutates shared closure (image only on first invocation) → copies dict before pop (image every invocation) | low | v2 dd/beacon/bot.py:343-356 vs v3 extensions/user_commands.py:213-220 |
| anchor/bungie_api | formatting | `from_vendors_api_response` skips sale_items when `manifest_table` None | always builds sale_items → wrapped in `if manifest_table is not None`; returns empty list with only `manifest_entry` | low | v2 bungie_api.py:890-900 vs v3 extensions/bungie_api.py:882-893 |
| anchor/bungie_api | formatting | `from_vendors_api_response` location lookup guarded | computes location whenever index valid (KeyError if `manifest_table` None) → `manifest_table is not None and ...` guard, location None when absent | low | v2 bungie_api.py:875-881 vs v3 extensions/bungie_api.py:859-869 |
| anchor/bungie_api | lost-feature | `MissingResponseField` exception removed | `request_from_api` raised descriptive `MissingResponseField('Response', ...)` → does `response['Response']` directly, bare `KeyError` on malformed/offline payload | low | v2 bungie_api.py:239-259,841-848 vs v3 extensions/bungie_api.py:825-828 |
| anchor/lost_sector | formatting | details command description text | "...are sent out" → "...are enabled" | low | v2 dd/anchor/lost_sector.py:69 vs v3 extensions/lost_sector.py:77 |
| anchor/posts | flags | Guard against `build_embed_with_user` returning None | no None-check (embed assumed built) → `if embed is None: return` before send/edit | low | v2 dd/anchor/posts.py:43-45,89-93,113-117 vs v3 extensions/posts.py:40-41,98-99,122-123 |
| anchor/posts | flags | `hidden=True` not carried over | post group/create/edit/copy declared `hidden=True` → no hidden flag (commands remain guild-scoped to admin guilds) | low | v2 dd/anchor/posts.py:28,37,72,100 vs v3 extensions/posts.py:28,32,75,104 |
| beacon/debug (DROPPED) | lost-feature | `debug legacy_follow` — bulk-add legacy mirror rows for existing prefix-matched channels | follow existing channels without creating them → only available as side effect of `/testing mirror create` (always creates channels first); cannot register existing channels | low | v2 dd/beacon/modules/debug.py:43-68 vs v3 extensions/testing.py:200-232 |
| beacon/debug (DROPPED) | lost-feature | `debug legacy_unfollow` — bulk-remove legacy mirror rows for existing prefix-matched channels | unfollow existing channels without deleting them → `/testing mirror delete` removes rows but also deletes the Discord channels; no way to drop wiring while keeping channels | low | v2 dd/beacon/modules/debug.py:71-96 vs v3 extensions/testing.py:269-299 |

## Cross-cutting checks

### (a) cfg.test_env consistency for guild-scoped commands

Every guild-scoped command/group found in the audit, and whether `cfg.test_env` is woven into its guild list:

| Module | Command / group | v3 guild scope | test_env woven? |
|---|---|---|---|
| beacon/mirror | `mirror` group (incl. delete_msg, source_details, undo_auto_disable, manual_add) | `[*cfg.test_env, cfg.control_discord_server_id]` | Yes |
| beacon/statistics | `stats` group (populations, server_list, autoposts) | `[cfg.control_discord_server_id]` | **No** — control guild only, test_env not woven in |
| beacon/user_commands | command group (preview/add/delete/edit/rename) | `[cfg.control_discord_server_id]` (dynamic user commands register to `cfg.test_env`) | Partial — group is control-only; dynamically-created user commands correctly use `cfg.test_env` (pre-fixed) |
| beacon/testing (NEW) | `testing` group + `mirror` subgroup | `[*cfg.test_env, cfg.control_discord_server_id]` | Yes |
| anchor/controller | all_stop, restart, info | `[cfg.control_discord_server_id]` | **No** — control guild only |
| anchor/posts | post create/edit/copy | `[kyber, control]` | **No** — kyber + control |
| anchor/lost_sector | `ls_update`; autopost control group | control guild (group `[control]`, dev_ prefix in test_env) | **No** on ls_update (control only); autopost group control-only |
| anchor/xur, eververse, gunsmith | autopost control groups | `[cfg.control_discord_server_id]` (re-applied per caller via loader.command) | **No** — control guild only |
| anchor/bungie_api | `bungie` group (login, account_numbers) | global (no guild scoping, both versions) | n/a — global |

Notes: the `mirror` and `testing` groups are the canonical `[*test_env, control]` pattern. The anchor control/autopost groups and `beacon/statistics` deliberately scope to control-guild-only and do **not** weave in `test_env`; this is consistent with each other but differs from the mirror/testing convention. `beacon/statistics` is the notable case where a previously-global `hidden=True` group became control-guild-only (flagged needs-judgment above). The two already-fixed test_env regressions (dynamic user commands → `cfg.test_env`, `layer_autocomplete` await) are correct in v3 and not re-flagged.

### (b) Anchor autoposter cron schedules (v2 vs v3)

| Autoposter | v2 cron | v3 cron | Test crontab `* * * * *` | Match? |
|---|---|---|---|---|
| xur | `0 17 * * FRI` | `0 17 * * FRI` | commented out (both) | Match |
| eververse | `0 17 * * TUE` | `0 17 * * TUE` | commented out (both) | Match |
| lost_sector | (cron identical both sides) | (cron identical both sides) | commented out (correctly) | Match |
| gunsmith | `1 17 * * TUE` | `1 17 * * TUE` | commented out (both) | Match |

No cron mismatches and no left-enabled `* * * * *` test crontab in any anchor autoposter. The listener migration `lb.LightbulbStartedEvent → h.StartedEvent` across these modules is mechanical and does not affect scheduling.

## Coverage appendix

- beacon/ada (pair) — no divergence
- beacon/autoposts (pair) — 7 findings
- beacon/emblems_and_cosmetics (pair) — no divergence
- beacon/eververse (pair) — no divergence
- beacon/free_games (pair) — 1 finding
- beacon/gunsmith (pair) — no divergence
- beacon/help (pair) — 7 findings
- beacon/iron_banner (pair) — no divergence
- beacon/lost_sector (pair) — no divergence
- beacon/mirror (pair) — 12 findings
- beacon/mirror_tracing (pair) — 1 finding
- beacon/nightfall (pair) — no divergence
- beacon/source (pair) — 1 finding
- beacon/statistics (pair) — 2 findings
- beacon/template (pair) — 3 findings
- beacon/trials (pair) — no divergence
- beacon/twab (pair) — 2 findings
- beacon/user_commands (pair) — 11 findings
- beacon/weekly_reset (pair) — no divergence
- beacon/xur (pair) — 1 finding
- beacon/__main__ (pair) — no divergence
- beacon/guild_count_status (NEW in v3, re-scoped from v2 __main__) — 1 finding
- beacon/testing (NEW) — no divergence
- beacon/debug (DROPPED) — 2 findings
- beacon/mirror_temp (DROPPED) — no divergence
- anchor/bungie_api (pair) — 6 findings
- anchor/controller (pair) — no divergence
- anchor/eververse (pair) — no divergence
- anchor/gunsmith (pair) — no divergence
- anchor/help (pair) — 3 findings
- anchor/lost_sector (pair) — 4 findings
- anchor/posts (pair) — 6 findings
- anchor/source (pair) — no divergence
- anchor/xur (pair) — 5 findings
- anchor/autopost (pair) — 1 finding
- anchor/embeds (pair) — 4 findings
- anchor/utils (pair) — 4 findings
- anchor/search_json (pair) — no divergence
- anchor/__main__ (pair) — 5 findings
---

## Auditor's verification notes (spot-checked)

Three findings were manually verified against both trees:

- **`anchor/bungie_api` `account_numbers` lost `auto_defer` (the sole `regression`)** —
  confirmed real. v2 `dd/anchor/bungie_api.py:1094` sets `auto_defer=True`; the v3
  `AccountNumbers` command neither sets it nor calls `ctx.defer()`, so the token refresh +
  Bungie API round-trips run before the first response and can blow the 3 s ack window.
- **`beacon/statistics` scope** — confirmed. v2 `bot.command(stats_command_group)` was
  global+`hidden=True`; v3 is `loader.command(..., guilds=[cfg.control_discord_server_id])`
  with **no `cfg.test_env`** — the same class as the already-fixed user_commands bug, so
  stats commands won't appear in a test guild.
- **`anchor/xur` content** — confirmed as v2-ahead-of-fork (see provenance caveat), not a
  rewrite drop. The Hawkmoon `[2]` value is the *fixed* one per v2 commit `be5a66a`, so v3
  currently carries the pre-fix `[1]`.

Findings are real divergences, not framework noise. **No code was changed in this audit
pass** — fixes are deferred to a follow-up per the agreed plan.
