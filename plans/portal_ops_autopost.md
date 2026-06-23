# Plan: Portal Ops autopost + command (daily/weekly featured ops & rewards)

> **Status:** Investigated + design decisions confirmed, not implemented. API
> surface verified live against the dev account (2026-06-23). See *Decisions* for
> the agreed scope/cadence/rewards; the only open item is the Pinnacle Ops
> fixed-rotation seed (Phase 1).
>
> **⚠️ Before implementing, re-verify by symbol name (not line number).** This repo
> is on an active feature branch (`feature-lightbulb-v3`) and the
> `dd/anchor/extensions/bungie_api` package was recently reorganized. Grep for the
> functions/classes named below before editing. Re-run the investigation scripts
> (see *How this was investigated*) to confirm the live API shape hasn't drifted —
> Bungie changes component payloads between seasons.
>
> **Hard gate (per [[verify-data-set-before-formatting]]):** Phase 1 must end by
> sending the user the **list of featured ops + reward items that would be posted**
> so they can sanity-check against the in-game Portal before any formatting is built.

## Goal

Surface the Destiny 2 **Portal** "Ops" featured rotation and their guaranteed
rewards as (a) a scheduled **autopost** and (b) a **slash command**, mirroring the
existing Xûr / Gunsmith / Eververse model. This is the "daily ops" table that
community posts (e.g. the referenced Reddit daily-ops summary) show: each featured
op → its guaranteed weapon/armor drop.

## What the Portal is (context)

The Portal (Edge of Fate, 2025) replaced the old director with category "tabs":
**Solo Ops**, **Fireteam Ops**, **Pinnacle Ops**, plus PvP (**Crucible**,
**Gambit**, **Trials**, **Iron Banner**) and the legacy strike playlist
(**Vanguard Ops**). Each category surfaces a **featured** activity that rotates
(daily for Solo/Fireteam/Vanguard, weekly for Pinnacle/PvP) and grants a
**guaranteed featured reward** plus bonus engrams. Difficulty tiers and toggleable
"skulls" (augments) scale rewards.

---

## API surface (VERIFIED LIVE)

**Authoritative source: `GetProfile` component `204` (CharacterActivities).** No
vendor, milestone, or presentation-node endpoint exposes the Portal rotation; the
in-world `availableActivityInteractables` are mission entry points, not Portal tabs,
and `GetPublicMilestones` does **not** list ops. Everything needed is in
`characterActivities.data.{characterId}.availableActivities`.

### Endpoint

```
GET /Platform/Destiny2/{membershipType}/Profile/{membershipId}/?components=204
    headers: X-API-Key, Authorization: Bearer <token>
```

(Component 204 is enough; the investigation also pulled 100,200,202,205 for context.)

### `characterActivities.data.{characterId}` new Edge-of-Fate keys

- `availableActivities[]` — the per-activity list (see below).
- `availableActivityInteractables[]` — in-world objects; **not useful** for Portal tabs.
- `difficultyTierCollections{}` — per-activity difficulty tiers + their fixed skulls.
- `selectableSkullCollections{}` — 279 collections of toggleable "skulls" (augments).
  Skull `hash`es do **not** resolve as `DestinyActivityModifierDefinition` /
  `DestinySandboxPerkDefinition` — they're a new EoF entity type; **not needed** for
  the rewards feature.

### `availableActivities[]` entry — the fields that matter

```jsonc
{
  "activityHash": 1604785891,          // -> DestinyActivityDefinition
  "isFocusedActivity": true,           // ★ marks the FEATURED (rotating) op
  "difficultyTier": 2,
  "modifierHashes": [1783825372],      // 1783825372 is an unnamed internal skull (ignore)
  "visibleRewards": [
    { "rewardItems": [
        { "itemQuantity": { "itemHash": 2191451996 },
          "uiStyle": "daily_grind_guaranteed" },   // ★ the guaranteed featured drop
        { "itemQuantity": { "itemHash": 3956025454 },
          "uiStyle": "extra_engram" }              // "Ops Bonus Drop" bonus engram
    ] }
  ]
}
```

- **`isFocusedActivity: true`** is the featured-op flag. Filter on it.
- **`visibleRewards[].rewardItems[]`** carries the rewards, distinguished by
  `uiStyle`:
  - `daily_grind_guaranteed` → the **featured guaranteed reward** (the weapon/armor
    to show), resolved via `DestinyInventoryItemDefinition[itemHash].displayProperties.name`.
  - `extra_engram` → "Ops Bonus Drop" (a generic bonus engram; same item hash
    `3956025454` everywhere — probably skip or show once).

### Categorisation signal (verified, but messy)

`DestinyActivityDefinition[activityHash]` gives `activityTypeHash`
(→ `DestinyActivityTypeDefinition.displayProperties.name`) and
`matchmaking.maxParty`. These do **not** map 1:1 to the in-game Portal tabs.
Observed live snapshot (deduped logically; raw had Matchmade/Customize and
Normal/Master duplicates):

| activityType name | maxParty | maps to Portal tab |
|---|---|---|
| `Solo Ops` | 1 | **Solo Ops** |
| `Mission`, `Crawl`, `Onslaught`, `Exotic Mission` | 3 | **Fireteam Ops** |
| `Vanguard Op` | 3 / 6 | strike playlist (shows under Solo & Fireteam) |
| `Seasonal Arena` (Ketchcrash) | 6 | **Fireteam Ops** (seasonal) |
| `Gambit` / `Trials of Osiris` | 4 / 3 | **Gambit** / **Trials** |

**No `isFocusedActivity` Pinnacle Ops entry appeared** in the snapshot. Pinnacle
Ops featured = the weekly featured raid/dungeon/GM, which per
[[bungie-api-no-featured-raid-dungeon]] the API does **not** expose — it must be
computed from a fixed rotation. Treat Pinnacle Ops as out-of-scope or
fixed-rotation, separately from the component-204 data.

**Recommended bucketing:** `maxParty == 1` → Solo Ops; PvE `maxParty >= 3` →
Fireteam Ops; mode in {Gambit, Trials, Crucible, Iron Banner} → that PvP tab. Keep
the activity-type name as a sub-label. A small hardcoded `activityTypeHash → tab`
override map will likely be needed for edge cases (e.g. Vanguard Op appearing under
both). **Confirm the exact grouping with the user against the live list (Phase 1).**

### Dedup required

The raw focused list (22 entries in the snapshot) contains:
- **Matchmade vs Customize** pairs of the same base activity → identical reward.
- **Quickplay Normal vs Master** → identical reward.
- The same featured drop surfaced across multiple characters.

Dedup by **(guaranteed reward itemHash, base activity name without the
`: Matchmade`/`: Customize` suffix)**.

---

## Reusable building blocks (no changes needed)

- `dd/anchor/extensions/bungie_api/oauth.py` — `refresh_api_tokens(runner)` (DB-backed,
  `with_login=False` needs no webserver; pass the module `get_webserver_runner()`),
  reads/rotates the refresh token in `schemas.BungieCredentials`.
- `dd/anchor/extensions/bungie_api/models.py` — `DestinyMembership.from_api(session,
  token)` and `.get_character_id(session, token, character_class)`.
- `dd/anchor/extensions/bungie_api/manifest.py` — `_get_latest_manifest`,
  `_build_manifest_dict` (loads manifest tables into an in-memory dict keyed by hash).
- `dd/anchor/extensions/bungie_api/constants.py` — `API_ROOT`, component string,
  `manifest_table_names` (list to extend — see below).
- `dd/anchor/autopost.py` — `make_autopost_control_commands(autopost_name,
  enabled_getter, enabled_setter, channel_id, message_constructor_coro,
  message_announcer_coro)` → builds the `/<name>` group with `auto`/`send`/`show`
  subcommands. Used by gunsmith.
- `dd/anchor/extensions/xur.py` — `api_to_discord_announcer(...)` (the announcer used
  by API-backed autoposts) and `fetch_vendor_data(...)` (template for the fetch
  helper; not directly reusable — it's vendor-specific).
- `dd/beacon/extensions/autoposts.py` — `follow_control_command_maker(followable_channel,
  autoposts_name, friendly_name, desc)` (beacon-side per-guild follow toggle).
- `dd/beacon/extensions/lost_sector.py` — `setup_nav_pages(...)` +
  `make_navigator_command(...)` (beacon navigator command over the mirrored channel
  history); daily `REFERENCE_DATE` pattern. `dd/beacon/extensions/weekly_reset.py` for
  the weekly `REFERENCE_DATE` (Tue 17:00 UTC).
- Scheduling: `aiocron.crontab("0 17 * * *", start=True)` (daily) /
  `"1 17 * * TUE"` (weekly), declared inside a `@loader.listener(h.StartedEvent)`
  handler (see gunsmith/lost_sector).
- Config: `cfg.followables["portal_ops"]` (new followable channel id from env) and a
  new `AutoPostSettings.get_portal_ops_enabled` / `set_portal_ops`.

### Manifest tables to add (constants.py `manifest_table_names`)

Need name resolution for activities, types, and reward items. Two options:
- **A — extend the manifest dict** (matches existing code): add
  `DestinyActivityDefinition`, `DestinyActivityTypeDefinition`. (`DestinyInventoryItemDefinition`
  is already loaded for reward-item names.) Adds memory but consistent with Xûr/Eververse.
- **B — live entity resolution** (`/Destiny2/Manifest/{entity}/{hash}/`): only a
  handful of hashes per post; far lighter; what the investigation scripts used.
  Cleaner for a low-cardinality feature like this. **Recommended.**

---

## Architecture (follows the Xûr/Gunsmith model)

API-backed, so **anchor fetches + formats + autoposts; beacon mirrors**:

1. **`dd/anchor/extensions/portal_ops.py`** (new)
   - `fetch_portal_ops(runner) -> list[PortalOp]` — refresh token → membership →
     character ids (one per class is enough; iterate classes only if focused sets
     differ) → GET component 204 → collect `isFocusedActivity` entries → dedup →
     resolve activity/type/reward names (Option B live, or via manifest dict) →
     bucket into tabs.
   - `portal_ops_message_constructor(bot) -> HMessage` — render the deduped, bucketed
     list. **Build only after the Phase 1 gate.**
   - Autopost cron in `@loader.listener(h.StartedEvent)` + `make_autopost_control_commands(...)`.
2. **`dd/beacon/extensions/portal_ops.py`** (new)
   - `follow_control_command_maker(cfg.followables["portal_ops"], "portal_ops", ...)`.
   - `setup_nav_pages(...)` + `make_navigator_command(...)` for the user command.
3. **`dd/common/schemas.py`** — add `AutoPostSettings.get_portal_ops_enabled` /
   `set_portal_ops` (mirror the gunsmith convenience methods).
4. **Config/env** — new `followables["portal_ops"]` channel id (`.env-example`, cfg).

---

## Phase 1 — data path + REPORT (gate)

Implement only `fetch_portal_ops` (no formatting). Produce and send the user a plain
list: for each featured op → **tab, activity name, guaranteed reward item, tier**.
Confirm bucketing + dedup + cadence against in-game before Phase 2.

## Phase 2 — formatting + autopost + command

After confirmation: build `portal_ops_message_constructor`, wire the anchor autopost
(cron at the agreed cadence) and control group, then the beacon follow command +
navigator. Add the new `AutoPostSettings` methods and `followables` entry. Manual-test
per `plans/v3_manual_testing_checklist.md`. Dev anchor needs a Bungie login first
([[dev-anchor-needs-bungie-oauth]]).

## Phase 3 — cleanup

Once this feature is implemented and verified, **delete the `scratch/` dir**
(investigation scripts + raw dumps) — it was kept only as reference for building this
feature. Also remove the `scratch/` entry from `.gitignore`.

---

## Decisions (confirmed with user 2026-06-23)

1. **Scope** — cover **all** tabs: **Solo Ops, Fireteam Ops, Vanguard + PvP
   (Gambit/Trials/Iron Banner), and Pinnacle Ops.**
2. **Cadence** — **one daily post at daily reset (`0 17 * * *` UTC) showing the
   current featured state.** No separate weekly post; weekly-rotating tabs just
   show whatever is currently featured in the daily post.
3. **Pinnacle Ops** — in scope but **not in the component-204 data**. Source it via a
   **fixed-rotation computation** ([[bungie-api-no-featured-raid-dungeon]]), since the
   API doesn't expose the weekly featured raid/dungeon/GM. This is a distinct code
   path from the rest; design it separately (a hardcoded weekly rotation table keyed
   off the weekly-reset anchor date). **Re-confirm the rotation contents with the
   user during Phase 1** — needs the current week's pinnacle featured as a seed.
4. **Rewards** — show **only** the `daily_grind_guaranteed` featured weapon/armor.
   **Drop** the `extra_engram` "Ops Bonus Drop" line (generic/identical across ops).
5. **Manifest** — still recommend **Option B (live entity resolution)**; low
   cardinality per post. (Implementer's call; not user-facing.)

### Phase 1 gate output (per these decisions)

Report, grouped by tab (Solo / Fireteam / Vanguard / Gambit / Trials / Iron Banner /
Pinnacle), each featured op → its `daily_grind_guaranteed` reward item name, deduped.
For Pinnacle Ops, report the proposed fixed-rotation entry for the current week for
the user to verify in-game.

## How this was investigated (reproduce)

Throwaway scripts in `scratch/` (gitignored), run via
`railway run uv run python scratch/<name>.py` (sandbox **off** — uv cache is
read-only under the sandbox, per [[railway-deploys-need-sandbox-disabled]]). Dev DB
has valid Bungie creds (refresh token good to 2026-09). Scripts:
`portal_investigate.py` (broad profile + milestone dump),
`portal_deepdive.py` (focused ops + rewards + uiStyles),
`portal_interactables.py` (interactables/milestones — ruled out),
`portal_categorize.py` (categorisation signals). Raw dumps in `scratch/out/`.
