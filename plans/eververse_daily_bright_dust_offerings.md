# Eververse: add Daily Bright Dust Offerings to the API post

> **Status:** Not started — design approved, ready to implement in two phases.
>
> **Hard gate:** Phase 1 must end by sending the user the **list of items that would
> be added** (so they can double-check in-game that nothing is missed). Do **not**
> build the post formatting until the user confirms the list.
>
> **Before implementing, re-verify by symbol name (not line number)** — grep for the
> functions named below; `dd/anchor/extensions/bungie_api` was recently reorganized.

## Context

The anchor eververse autopost (`dd/anchor/extensions/eververse.py`) currently fetches
one vendor — "This Week at Eververse" (`vendorHash=3361454721`) — and renders a
**weekly** Bright Dust Offerings section. We want to also surface the **daily bright
dust offerings**: exotic/legendary cosmetics (ghosts, sparrows, ships, ornaments,
etc.) sold by several **rotator** vendors. These vendors are identifiable in the
Destiny manifest by a `vendorIdentifier` starting with
`EVERVERSE_BRIGHT_DUST_ROTATOR` (e.g. `EVERVERSE_BRIGHT_DUST_ROTATOR_EXOTIC_GHOSTS`).

The fetch/parse machinery already exists and is fully reusable
(`DestinyVendor.request_from_api`, `DestinyItem.from_sale_item`, the manifest dict).
The only new work is discovering the rotator vendor hashes, fetching them, and
appending a new section to the existing weekly post.

**Confirmed decisions:**
- Match rotator vendors by **`vendorIdentifier` prefix only** (`EVERVERSE_BRIGHT_DUST_ROTATOR`).
- Render as a **new section inside the existing weekly post** (snapshot at post time);
  no new schedule/toggle/channel.
- Present items as a **flat list**, each line showing the item's **type**.
- **Ornament → base-item link only for EXOTIC ornaments.** Legendary (universal)
  ornaments apply to a class+slot rather than a single item, so they get **type only,
  no link**.

## Reusable building blocks (no changes needed)

- `dd/anchor/extensions/bungie_api/manifest.py` — `_get_latest_manifest`,
  `_build_manifest_dict` (loads `DestinyVendorDefinition` keyed by hash; each value has
  `vendorIdentifier`, `factionHash`, `displayProperties.name`).
- `dd/anchor/extensions/bungie_api/models.py` — `DestinyVendor.request_from_api`
  (raises `VendorNotFound` per vendor), `DestinyItem.from_sale_item`,
  `DestinyItem.{name, hash, costs, item_type_friendly_name, lightgg_url, is_exotic}`.
- `dd/anchor/extensions/bungie_api/oauth.py` / `models.py` — `refresh_api_tokens`,
  `DestinyMembership.from_api` / `get_character_id`.
- `dd/anchor/extensions/eververse.py` — `eververse_message_constructor`,
  `format_eververse_vendor` (existing bright-dust cost filter idiom:
  `"bright dust" in str(item.costs).lower()`, cost read as `item.costs['Bright Dust']`).
- `dd/anchor/search_json.py` — dev manifest-inspection utility pattern.

---

## Phase 1 — Discovery + fetch, then REPORT the item list (gate)

Implement the data path and use it to produce a list for the user; **stop and confirm
before Phase 2**.

1. **Discover rotator hashes** (pure helper in `eververse.py`):
   ```python
   _DAILY_BRIGHT_DUST_ROTATOR_PREFIX = "EVERVERSE_BRIGHT_DUST_ROTATOR"

   def _bright_dust_rotator_hashes(manifest_table) -> list[int]:
       return [
           v["hash"]
           for v in manifest_table["DestinyVendorDefinition"].values()
           if v.get("vendorIdentifier", "").startswith(_DAILY_BRIGHT_DUST_ROTATOR_PREFIX)
       ]
   ```
   (Optionally promote the prefix into `bungie_api/constants.py` beside `XUR_VENDOR_HASH`.)

2. **Fetch helper** (`eververse.py`) — builds the shared context once and loops
   `request_from_api`, skipping vendors that 404. `xur.fetch_vendor_data` can't be
   reused directly: it rebuilds the manifest internally, can't discover hashes first,
   and its `accumulate` can't skip a `VendorNotFound` vendor.
   ```python
   async def fetch_daily_bright_dust_offerings(runner) -> list[api.DestinyItem]:
       token = await api.refresh_api_tokens(runner)
       async with aiohttp.ClientSession() as session:
           membership = await api.DestinyMembership.from_api(session, token)
           character_id = await membership.get_character_id(session, token, "Hunter")
       manifest_table = await api._build_manifest_dict(
           await api._get_latest_manifest(schemas.BungieCredentials.api_key)
       )
       items: dict[int, api.DestinyItem] = {}            # dedupe by item hash
       for vendor_hash in _bright_dust_rotator_hashes(manifest_table):
           try:
               vendor = await api.DestinyVendor.request_from_api(
                   access_token=token, destiny_membership=membership,
                   character_id=character_id, vendor_hash=vendor_hash,
                   manifest_table=manifest_table,
               )
           except api.VendorNotFound:
               continue                                  # rotator not currently active
           for it in vendor.sale_items:
               if "bright dust" in str(it.costs).lower():
                   items[it.hash] = it
       return list(items.values())
   ```
   - Single character/class (Hunter) — these cosmetics are class-agnostic.

3. **GATE — report to the user.** Run the path in a dev shell (needs a Bungie OAuth
   login) and send the user:
   - the matched rotator `vendorIdentifier`s + vendor names (so they confirm we found
     **all** the rotators), and
   - the full candidate item list: **name · type (`item_type_friendly_name`) · rarity ·
     Bright Dust cost · source rotator**.

   The user double-checks in-game for missing/extra items. **Only proceed to Phase 2
   after they confirm.** This guards against missed vendors/items.

---

## Phase 2 — Render the section + wire in (after confirmation)

1. **Extend `format_eververse_vendor`** to accept `daily_items` (and the
   `manifest_table`, needed for the exotic-ornament lookup) and append, after the
   existing weekly sections and before `substitute_user_side_emoji`:
   ```python
   if daily_items:
       description += "**__DAILY BRIGHT DUST OFFERINGS__** :bright_dust:\n\n"
       for item in daily_items:
           line = (
               f"• [{item.name}]({item.lightgg_url}) "
               f"({item.costs['Bright Dust']}) — {item.item_type_friendly_name}"
           )
           if item.is_exotic:
               target = _exotic_ornament_target_name(item, manifest_table)
               if target:
                   line += f" for {target}"
           description += line + "\n"
       description += "\n"
   ```
   - **Item type**: `item_type_friendly_name` (manifest `itemTypeDisplayName`) — already
     populated; covers the type requirement for every item.
   - **Ornament target — EXOTIC ONLY** (`_exotic_ornament_target_name`): attempt to
     resolve the specific exotic weapon/armor an exotic ornament applies to; legendary
     ornaments are left as type-only. The exact manifest linkage must be confirmed from
     real data (see below); fall back to no suffix when unresolved.

2. **Wire into `eververse_message_constructor`:**
   ```python
   daily_items = await fetch_daily_bright_dust_offerings(api.get_webserver_runner())
   ...
   return await format_eververse_vendor(eververse_data, bot, daily_items=daily_items)
   ```
   No changes to scheduling, the `eververse` toggle, the channel, or the
   `make_autopost_control_commands` group — the section rides along in the weekly post
   and `/eververse show` / `/eververse send`.

### Resolving the exotic-ornament target (confirm on live manifest)

The base item an ornament applies to is **not a single field** on the ornament's
`DestinyInventoryItemDefinition`; exotic weapon ornaments link via socket/plug sets.
Using the manifest dump from Phase 1's gate, determine the reliable signal for **exotic
ornaments** (candidates: a reverse lookup from the exotic weapon/armor's socket plug
sets to the ornament hash; `plug.plugCategoryIdentifier`; or `flavorText`/description).
Implement `_exotic_ornament_target_name` from whatever that inspection shows, and skip
the suffix when no reliable link exists.

---

## Critical files

- `dd/anchor/extensions/eververse.py` — new `_bright_dust_rotator_hashes`,
  `fetch_daily_bright_dust_offerings`, `_exotic_ornament_target_name`; extend
  `format_eververse_vendor` + `eververse_message_constructor`.
- (optional) `dd/anchor/extensions/bungie_api/constants.py` — the rotator prefix
  constant.

## Verification

Live Bungie OAuth + manifest required; no DB-free unit test is feasible.

1. **Phase 1 gate:** dev-run prints matched rotators + candidate item list; user
   confirms completeness in-game **before** Phase 2.
2. **Render:** anchor `/eververse show` shows the new "Daily Bright Dust Offerings"
   flat list with item types; exotic ornaments show `for <exotic>`, legendary
   ornaments show type only.
3. **Resilience:** inactive rotators (`VendorNotFound`) are skipped without failing the
   post; an empty result simply omits the section.
4. **Lint/types:** `uv run ruff check dd` and `uv run ty check dd` clean.
5. **Regression:** existing weekly section, `/eververse` toggle, and schedule unchanged.
