# Automating the Kyber's Corner "Weekly Reset Overview"

**Provenance.** Built from 3 real posts (2026-06-16/23/30) fetched from channel
`615429125955387392` with the prod beacon token (`railway run -e production -s beacon`;
token never printed), plus a per-field Bungie-API feasibility study: **11 fields, each
researched and adversarially verified** (a first pass lost 3 verifiers + synthesis to a
usage-limit; those `crucible-rotators` / `pantheon` / `image-and-manual` verifiers were
re-run and confirmed **semi / semi / manual** — no verdict changed — and this report is the
re-synthesis over the complete, verified set). Verdicts are reconciled against the actual
`dd/` codebase.

## 0. The one structural decision

`weekly_reset` is today a **producer-less followable**: a human drops the post into channel
`615429125955387392` (`cfg.followables["weekly_reset"]`) and beacon mirrors/paginates it
(`dd/beacon/extensions/weekly_reset.py`, which only wires `setup_nav_pages` +
`follow_control_command_maker`). "Automate the post" therefore = **add one anchor producer
extension**, `dd/anchor/extensions/weekly_reset.py`, structurally a sibling of `xur.py` /
`portal_ops.py`. Once it posts with `crosspost=True` via `xur.api_to_discord_announcer`,
beacon mirrors automatically — **zero beacon changes**.

Everything below feeds that one new extension.

## 1. Field-by-field mapping

Effort is measured in *new* work given the infra that already ships. Endpoints: **PM** =
`GET /Platform/Destiny2/Milestones/` (GetPublicMilestones, `X-API-Key` only); **MAN** =
manifest def (`DestinyActivityDefinition` etc.); **V402** = authenticated `GetVendor` comp
`400,402` (already shipping for Xûr); **P204** = authenticated `GetProfile` comp `204`
(already shipping in `portal_ops.py`).

| # | Post field | Final source | Verdict | Conf | Effort |
|---|---|---|---|---|---|
| 1 | Reset timestamp | **Computed** — floor-to-grid Tue 17:00 UTC (reuse `xur.xur_departure_string` logic / `REFERENCE_DATE`) | auto | high | trivial |
| 2 | GM Nightfall **strike name** | **PM** → nightfall milestone → **MAN** `DestinyActivityDefinition.originalDisplayProperties.name` (GM/highest tier) | auto | med-high | med |
| 3 | GM Nightfall **reward weapon** | **V402** Zavala vendor `69482069` (Xûr pattern) → `DestinyItem` → name + `lightgg_url` | **auto** (was "semi/needs-OAuth" — false) | med | med |
| 4 | Vanguard Quickplay featured weapon | **P204** `visibleRewards[].rewardItems[]` `_guaranteed` → `portal_ops.fetch_portal_ops()` Fireteam Ops bucket | **auto** (was semi) | high | low |
| 5 | Crucible Control featured weapon | **P204** same, Crucible bucket | **auto** (was semi) | high | low |
| 6 | Zavala's Weapon (w/ type) | **V402** Zavala `69482069` featured legendary weapon slot | **auto** (was semi) | med-high | med + slot rule |
| 7 | Seasonal raid/dungeon ("Desert Perpetual"/"Equilibrium") | **Pinned hash config → MAN** name resolve; PM liveness check | semi | high | low |
| 8 | Featured rotator raids (×2) | **Computed** from curated anchor + 4-pair cycle; editor-owned | semi | high | med |
| 9 | Featured rotator dungeons (×2) | **Computed** from 2 curated independent lists; editor-owned | semi | med | med |
| 10 | Pantheon Reprise/Encore | **Curated-primary** (`RotationData`) + optional **P204** cross-check | semi | med | med |
| 11 | Crucible 3v3 / 6v6 rotators (1v6 constant) | **P204** `availableActivities` + curated constants/name-cleanup | semi | high | med |
| 12 | Iron Banner / Trials schedule | **P204**/V402 live IB detect + curated per-episode IB-week list; Trials = inverse of IB | semi | high | med |
| 13 | light.gg deep-links | **Computed** — `DestinyItem.lightgg_url` (already shipped) | auto | high | trivial |
| 14 | Key-art image | **Manual** (`EVENT_IMAGE_MAP` + editor override), optional PM/IB pre-select | manual | high | low |
| 15 | EVENTS narrative / bug notes / extra links | **Manual** — irreducibly editorial | manual | high | low |

**Reconciliation of the "needs OAuth" errors (fields 3, 4, 5, 6):** three researchers rated
the featured weapons *semi/manual* on the premise that the project "only has an
unauthenticated public key." That premise is **false for this repo**. The full authenticated
path already ships and runs weekly in production:
- `dd/anchor/extensions/bungie_api/oauth.py::refresh_api_tokens()` mints access tokens from a
  self-rotating refresh token in `schemas.BungieCredentials` (no human in the loop), and the
  `ReadDestinyVendorsAndAdvisors` scope is already granted (Xûr needs it).
- `constants.API_VENDORS_AUTHENTICATED` requests components `"…,400,402"`;
  `client.fetch_vendor()` calls it; `models.DestinyVendor.from_vendors_api_response()` parses
  `Response.sales.data[].itemHash` → `DestinyItem`.
- `xur.py::fetch_vendor_data(runner, vendor_hashes, character_class)` is the ready-made
  driver: resolve membership → character → accumulate vendors. `fetch_xur_data` is literally
  `fetch_vendor_data(runner, [XUR_VENDOR_HASH, …])`.
- `portal_ops.py::fetch_portal_ops()` already reads authenticated P204 `visibleRewards` and
  returns per-tab `PortalOp(reward_name, reward_hash, reward_emoji, …)` for the
  Fireteam/Arena/Crucible buckets — i.e. the Vanguard-Quickplay and Crucible-Control featured
  weapons **are already being fetched today**.

So fields 4 and 5 are a *filter over an existing call*; fields 3 and 6 are a *near-copy of
`fetch_xur_data` with vendor hash `69482069`*. All four are **AUTO via the existing
Xûr/Portal pattern**.

**The one genuine semi-hinge:** the post shows **one** Zavala weapon, but Zavala now sells
several weekly options (comp 402 returns many `sales.data` entries). This needs a
deterministic **slot/category rule** — mirror how `format_xur_vendor` filters
`vendor.sale_items` by `item.is_legendary and item.is_weapon`, then pick the featured
category (or highest-tier). If no clean rule survives an episode change, it degrades to the
**human-confirm step** (§2), not to fully manual.

## 2. Recommended architecture

Three layers, all reusing shipped infra.

### 2a. Typed-slot template engine
A dataclass carrying every slot, then one pure renderer:

```python
# dd/anchor/extensions/weekly_reset.py
@dataclass
class WeeklyResetContext:
    reset_ts: int
    gm_strike: str;            gm_reward: DestinyItem | None
    quickplay_weapon: DestinyItem | None
    control_weapon: DestinyItem | None
    zavala_weapon: DestinyItem | None
    seasonal_raid: str;        seasonal_dungeon: str
    rotator_raids: tuple[str, str]
    rotator_dungeons: tuple[str, str]
    pantheon_reprise: str;     pantheon_encore: str
    crucible_3v3: list[str];   crucible_6v6: list[str]
    iron_banner: bool;         trials_line: str | None
    image_url: str | None
    events_narrative: str;     notes: list[str];   extra_links: list[dict]
```

`format_weekly_reset(ctx, emoji_dict) -> HMessage` builds the description string exactly like
`xur.format_xur_vendor` / `lost_sector.format_post` do, ending with
`substitute_user_side_emoji(emoji_dict, description)` (or
`re_user_side_emoji.sub(construct_emoji_substituter(...))` for CV2). Weapon lines reuse
`xur.weapon_line_format(weapon, include_weapon_type=True, include_lightgg_link=True, …)` so
`DestinyItem.lightgg_url`, `expected_emoji_name`, and `item_type_friendly_name` (the
"(fusion)"/"(pulse)" tag) come for free.

### 2b. Bungie fetchers (exact additions)
- **Add `API_MILESTONES = API_ROOT + "/Destiny2/Milestones/"`** to `constants.py` and a
  `client.fetch_public_milestones(session)` that GETs it with only `{"X-API-Key": …}` (mirror
  `manifest._get_latest_manifest`'s header shape). Used for the GM strike name and the
  seasonal-raid liveness check.
- **Manifest defs:** reuse `portal_ops._resolve_entity(session, "DestinyActivityDefinition"/
  "DestinyMilestoneDefinition"/"DestinyActivityTypeDefinition", hash, cache)` (the live
  per-hash `API_ENTITY` endpoint, `X-API-Key` only). This is **preferred over adding whole
  tables to `manifest_table_names`** — weekly_reset touches a handful of hashes/week, and
  `DestinyActivityDefinition` is one of the heaviest tables (memory + build cost in
  `_build_manifest_dict`). Only add tables if a whole-table scan is unavoidable (e.g.
  name-search for the Pantheon pool once per season).
- **Vendors:** `zavala = await xur.fetch_vendor_data(api.get_webserver_runner(), [69482069],
  "Titan")` → filter `zavala.sale_items` by the featured-legendary-weapon slot rule (§1
  hinge). This is the entire lift for fields 3 & 6.
- **Featured playlist weapons (4, 5):** `ops = await portal_ops.fetch_portal_ops()`; take
  `reward_name`/`reward_hash` from the `Fireteam Ops` and `Crucible` tabs. No new fetch.
- **Crucible rotators + IB (11, 12):** one P204 read (reuse `portal_ops.API_PROFILE_204` + the
  auth-header block in `fetch_portal_ops`), then classify
  `characterActivities.data.{cid}.availableActivities[]` by `matchmaking.maxParty` (12→6v6,
  6→3v3) and by Iron Banner activity-type, subtracting the constant playlists.

### 2c. Manual-override layer + human-in-the-loop
Register a **`weekly_reset` schema** in `rotation_schema.ROTATION_SCHEMAS` (the free web form
and validator come from that one entry). Wire the three per-type hooks in
`rotation_editor.py`: `_default_doc("weekly_reset")`, `_build_domain_object`,
`_render_preview`. The doc holds the fields the API can't derive: `rotator_anchor` +
`raid_pairs`/`dungeon_lists`, `seasonal_raid_hash`/`seasonal_dungeon_hash`, `pantheon`
reprise/encore pool, `ib_week_resets[]` + `trials_off_resets[]`, `image_url`/`event`,
`events_narrative`, `notes[]`, `extra_links[]`. Loading uses the exact
`RotationData.get_data("weekly_reset")` → last-known-good-cache pattern of
`lost_sector.load_rotation` / `xur.load_xur_locations`.

Two human-in-the-loop shapes (recommend running **both**):

- **Draft-review before publish (default weekly path).** Cron
  `@aiocron.crontab("0 17 * * TUE")` in a `StartedEvent` listener calls
  `xur.api_to_discord_announcer(bot, channel_id=cfg.followables["weekly_reset"],
  construct_message_coro=weekly_reset_message_constructor, check_enabled=True,
  enabled_check_coro=AutoPostSettings.get_weekly_reset_enabled, cv2=…)`.
  `weekly_reset_message_constructor` builds `WeeklyResetContext` by (a) auto-deriving fields
  1–13 from the fetchers, then (b) overlaying the editor's `RotationData` doc for the
  manual/semi fields. The editor confirms/edits via the existing `/rotation edit` web form
  **before** Tuesday; the placeholder→build→edit→crosspost loop in `api_to_discord_announcer`
  handles the rest.
- **Interactive confirm in Discord (`/weekly_reset send`).** Feed the auto-derived draft as
  `existing_nodes` into `cv2_builder.build_components_with_user(ctx, done_button_text="Post",
  existing_nodes=draft_nodes)`. The owner sees every slot pre-filled, tweaks the Zavala pick /
  prose / image inline, and hits Post. This is the clean answer to the Zavala one-of-several
  hinge and to any week the API drifts.

Control surface is free: `make_autopost_control_commands(autopost_name="weekly_reset",
enabled_getter=…, enabled_setter=AutoPostSettings.set_weekly_reset,
channel_id=cfg.followables["weekly_reset"],
message_constructor_coro=weekly_reset_message_constructor,
message_announcer_coro=xur.api_to_discord_announcer)` gives `/weekly_reset auto|send|show`.
Add `get_weekly_reset_enabled`/`set_weekly_reset` to `AutoPostSettings` (two 3-line methods
mirroring `get_portal_ops_enabled`/`set_portal_ops`; no migration — `AutoPostSettings` and
`RotationData` are both slug-keyed).

## 3. Phased rollout by ROI

**Phase 0 — plumbing (unlocks everything):** scaffold `dd/anchor/extensions/weekly_reset.py`;
add `AutoPostSettings.{get,set}_weekly_reset`; add the `weekly_reset` `ROTATION_SCHEMAS` entry
+ `rotation_editor` hooks; ship `WeeklyResetContext` + `format_weekly_reset`; wire the cron +
`make_autopost_control_commands`. Post is initially 100% editor-fed (parity with today), then
slots flip to auto.

**Phase 1 — highest ROI, pure-auto, trivial effort (fields 1, 13, 7):** reset timestamp
(compute), light.gg links (`lightgg_url` already exists), seasonal raid/dungeon (pin two
hashes, resolve names via `_resolve_entity`; ~1–2 edits/year). Deterministic, no auth,
near-zero drift.

**Phase 2 — auto via existing Xûr/Portal pattern, low-med effort (fields 4, 5, 3, 6, 2):**
playlist featured weapons from `fetch_portal_ops()` (lowest effort — data already fetched);
Zavala's Weapon + GM reward weapon from `fetch_vendor_data([69482069])` + the slot rule; GM
strike name from PM + manifest. This is where "manual every Tuesday" collapses to "auto," and
it reuses code that already runs in prod.

**Phase 3 — semi, live-read + curation, med effort (fields 11, 12):** crucible 3v3/6v6 from
P204 `availableActivities`; Iron Banner live-detect from P204/V402 with the curated IB-week
list, Trials as its inverse. Automatable but needs the constants/edge-case layer.

**Phase 4 — semi, curated-primary, med effort (fields 8, 9, 10):** rotator raids/dungeons
(deterministic compute from a **re-derived** anchor — see Risks — and per-season lists) and
Pantheon reprise/encore (curated `RotationData` pool, optional P204 cross-check). Right most
weeks; editor verifies against the reset.

**Last / stays MANUAL (the ~3):** (1) the **EVENTS narrative** prose, (2) the **bug/info
callouts** ("Duality is available due to a bug") and **extra kyberscorner links**, (3) the
**event key-art image** selection. No Bungie endpoint synthesizes human prose or supplies
clean marketing key art; these live permanently in the `RotationData` editorial doc +
`EVENT_IMAGE_MAP`. The Phase-4 semi fields also retain a *periodic* human touch (re-anchor
once/season, curate the Pantheon pool), and the Zavala pick keeps the confirm step — but
those are semi, not manual.

## 4. Risks

- **Manifest version drift.** `manifest._get_latest_manifest` caches by filename and never
  re-checks version mid-run; a stale local zip yields stale names. Prefer
  `portal_ops._resolve_entity`'s **live per-hash** resolution for weekly_reset (always
  current, avoids loading heavy tables). If you do add `DestinyActivityDefinition`/
  `DestinyMilestoneDefinition` to `manifest_table_names`, budget the memory/build-time hit.
- **Locale.** The manifest is pinned to `en` (`manifest.py` `mobileWorldContentPaths["en"]`).
  Prefix-stripping ("Grandmaster: "/"Nightfall: ") is language-specific — use
  `originalDisplayProperties.name`. For the weapon-type tag, prefer the locale-independent
  subtype (`DestinyItem.item_type_friendly_name` already drives `expected_emoji_name`) over
  parsing display strings.
- **API gaps (no unauth path).** Rotator raids/dungeons (8, 9) and Pantheon (10) are in **no**
  unauthenticated endpoint (confirmed by the verdicts and by `portal_ops`'s own docstring:
  "Bungie does not expose the weekly featured raid/dungeon rotator"). These stay
  compute+curated. Featured playlist weapons (4, 5) are character-scoped → not in
  `GetPublicVendors`; they require the authed P204 path (which ships).
- **⚠️ The research anchor for the rotators is wrong.** The verdict for field 8/9 shows
  `ANCHOR=1782234000→pair0` computes the *wrong* raids for the live week (off by ~1 week;
  wrong dungeon membership). **Do not copy the anchor/lists verbatim** — re-derive both from
  Kyber's own recent posts or a one-time authed P204 snapshot before shipping, and store them
  in the editor doc so they're fixable without a deploy.
- **light.gg links need item hashes.** Auto-sourced weapons (3, 4, 5, 6) carry `itemHash` →
  real deep-links via `DestinyItem.lightgg_url`, fixing today's bare-`https://www.light.gg/`
  placeholder. But any weapon a human types into the editor has **no hash** → give the
  `weekly_reset` schema an optional `item_hash` field per manual weapon (or resolve-by-name
  against `DestinyInventoryItemDefinition`) so curated entries deep-link too. Never GET
  light.gg to validate (Cloudflare 403s server-side) — emit-only.
- **Vendor/hash drift across episodes (Renegades/Portal).** Vendor `69482069`, Crucible
  `3603221665`, milestone hashes, and PvP bucket hashes can shift. Keep them as **editable
  config**, resolve vendors by name where practical, and re-verify each episode. The authed
  token's service character must have the vendor/activity available or
  `sales`/`availableActivities` come back empty (`client.fetch_vendor` already raises
  `VendorNotFound` for `ErrorCode 1627` → the announcer retry/last-known-good path absorbs it).
- **Semi fields failing silently.** Every loader in the repo (`lost_sector.load_rotation`,
  `xur.load_xur_locations`) falls back to a last-known-good cache; do the same for the
  `weekly_reset` doc so an API/parse hiccup degrades to the previous good value rather than a
  broken post, and log a drift warning when the computed rotators disagree with a PM
  cross-check.

**Net:** ~9 of the ~13 rotating slots become auto or compute (all four weapon fields
included, contrary to the "needs OAuth" verdicts), 3 remain human-curated prose/image, and
the remainder are deterministic-with-editor-confirm — all delivered by one new anchor
extension that reuses `oauth.refresh_api_tokens`, `client.fetch_vendor`,
`xur.fetch_vendor_data`, `portal_ops.fetch_portal_ops`, `RotationData`/`rotation_editor`,
`make_autopost_control_commands`, `api_to_discord_announcer`, and
`cv2_builder.build_components_with_user`.

**Key files to add/modify:** **new** `dd/anchor/extensions/weekly_reset.py`; **edit**
`dd/anchor/extensions/bungie_api/constants.py` (add `API_MILESTONES`; optionally two manifest
tables), `dd/anchor/extensions/bungie_api/client.py` (add `fetch_public_milestones`),
`dd/common/rotation_schema.py` (`weekly_reset` schema),
`dd/anchor/extensions/rotation_editor.py` (`_default_doc`/`_build_domain_object`/
`_render_preview`), `dd/common/schemas.py` (`AutoPostSettings.{get,set}_weekly_reset`). **No**
beacon changes — `dd/beacon/extensions/weekly_reset.py` mirrors the anchor post as-is.

---

## Implementation (shipped on `feat/weekly-reset-autopost`)

Built as a single new extension `dd/anchor/extensions/weekly_reset.py` (+ a
`GetPublicMilestones` helper in `bungie_api`, an `AutoPostSettings.get/set_weekly_reset`
pair, and a `WEEKLY_RESET_DRAFTS_CHANNEL_ID` config var). **No beacon changes.**

Flow (matches the requested UX): at Tue 17:00 UTC a cron derives what the API can give,
merges carried-over curated config, saves the draft, and posts a **live Components V2
preview card** to the drafts channel, @-mentioning the bot owners. The team runs
`/weekly_reset edit` — an **owner-only, ephemeral** editor: a section selector + select
menus for the pick-from-API fields (Zavala weapon, Pantheon bosses, IB/Trials toggles) and
modals for free text. Every edit persists to the DB and re-renders the card. **Publish**
shows a confirm/cancel, then sends the exact card (crossposted); beacon mirrors it.

Decisions worth recording:
- **Team = bot owners** (client-level `owner_only` hook); component presses are also
  gated (per-session custom_ids + a single-writer lock). Non-owners get nothing.
- **No autocomplete** — autocomplete interactions dispatch *before* the owner check, so
  they'd leak to non-owners. Select menus ("options") are used instead, which is fully
  gated and equally in-Discord.
- **Publish = one fresh `send_message(crosspost=True)`** with controls stripped (not the
  placeholder→edit announcer dance), so the mirror sees the final bytes.
- **Editor entry is the `/weekly_reset edit` slash command** (stateless, restart-proof)
  rather than a persistent card button, so a Railway deploy never leaves a dead button.
- The rotator anchor/order is re-derived and **unit-tested against all three sampled
  weeks**; only 3 weeks are ground-truth, so the 4th+ cycle entries are best-guess and
  fully editable in the editor.

To go live: set `WEEKLY_RESET_DRAFTS_CHANNEL_ID` (Railway/`.env`) and `/weekly_reset auto
enable`. While it's unset the whole feature stays dormant. The interactive Discord flow
still needs a live smoke test on dev (unit tests cover the pure logic, rendering,
serialisation, validation, reconciliation and the CV2 builder).

---

### Appendix — the 3 sampled posts

Author: `DD v1` bot; single rich embed each. Reset timestamps
`1782234000 → 1782838800 → 1783443600` (+604800s exactly, Tue 17:00 UTC). Rotating values
observed: GM Nightfall (Birthplace of the Vile/Whatchamacalit → Defiant Battleground: EDZ/
Salvager's Salvo → The Sunless Cell/Null Composure); Quickplay weapon (Punching Out →
Prolonged Engagement → Service Revolver); Control weapon (Joxer's Longsword → Better Devils →
Unending Tempest); rotator raids (King's Fall+Garden of Salvation → Root of Nightmares+Deep
Stone Crypt → Crota's End+Vault of Glass); rotator dungeons (Spire+Pit of Heresy → Ghosts of
the Deep+Prophecy → Warlord's Ruin+Grasp of Avarice); Pantheon (Gahlran/Consecrated Mind →
Calus/Morgeth → Argos/Insurrection Prime); Zavala weapon (Oxygen SR3 → Lionfish-4fr → Horror's
Least); Iron Banner present wk3 (Trials absent that week). All light.gg links were bare
`https://www.light.gg/` placeholders.
