# Plan — Lazy application-emoji store (item icons as inline emoji)

Status: **core built, green, verified — NOT yet integrated into posts or deployed.**
Collision gate PASSED. `make check` green (843 passed incl. 35 new). Live-verified the
`create_application_emoji(app, name, h.URL(bungie_icon))` upload path against dev beacon.

## What was built (this session)

- `dd/common/emoji_store.py` — `emoji_name()` (hashless slug) + `AppEmojiStore`
  (pure-lazy, self-warms on `StartedEvent`, upload-on-miss under a lock, safe LRU
  eviction, icon-change self-heal, start-reconcile with the live store).
- `dd/common/schemas.py` — `AppEmojiCache` table (PK `(app_id, name)`, `last_used` LRU
  index) + portable classmethods (upsert/touch/oldest/remove/all_for_app).
- `migrations/20260713165935.sql` — `CREATE TABLE app_emoji_cache` (hand-written; Atlas
  diff needs a MySQL scratch DB unavailable outside the dev container). `atlas.sum`
  rehashed in the worktree.
- `dd/anchor/extensions/bungie_api/models.py` — `DestinyItem.icon_path` + `.icon_url`.
- `dd/anchor/extensions/bungie_api/emoji.py` — `item_emoji(store, item)` bridge
  (Fable's placement call: free function in anchor; returns rendered `<:name:id>` or
  falls back to the legacy `:type:` token).
- `dd/common/utils.py` — `construct_emoji_substituter` now leaves already-qualified
  `<:name:id>` mentions untouched (Fable-flagged correctness fix).
- `dd/{beacon,anchor}/__main__.py` — construct + DI-register `AppEmojiStore`.
- Tests: `dd/common/tests/test_emoji_name.py`, `dd/anchor/tests/test_emoji_store.py`.

## Remaining (NOT done — needs user direction)

1. **Wire `item_emoji` into actual post sites** — nothing calls it yet. Candidates:
   `xur.py`, `eververse.py`, weekly-reset / hybrid producers. Anchor injects the store
   via DI; beacon posts must be handed `(emoji_name, icon_url)` (anchor persists these
   into rotation JSON — extend that shape) since beacon never sees `DestinyItem`.
2. **Apply the migration** (`make atlas-migration-apply`) on dev, then prod.
3. **Deploy** dev, smoke-test a real post, then prod (explicit confirmation required).
4. **Re-confirm CDN persistence after ~24h** (the eviction-safety assumption).
5. Leftover dev test emoji `anchortest` still sits in the anchor dev store — delete.

## Goal

Render Destiny item icons as **inline** custom emoji in bot messages (weekly reset, Xûr,
hybrid posts, rotation embeds). Inline is a hard requirement — CV2 CDN thumbnails were
ruled out because they can't sit inside a text run.

## Findings that shaped this design (all empirically verified on dev beacon/anchor)

- **App emojis are per-app and cross-bot use fails.** A bot can only inline-render emoji
  from **its own** application store; another bot's `<:name:id>` degrades to `:name:`
  text. Reactions with a foreign app emoji hard-fail (`400 Unknown Emoji`). So each bot
  needs the emoji it uses in its *own* store (≤2000/app).
- **Eviction is SAFE.** Deleting an app emoji frees the store slot but the CDN asset at
  `cdn.discordapp.com/emojis/{id}.png` **persists** (HTTP 200 after delete; bogus id =
  404), and already-posted messages keep rendering. So an LRU cache does **not** rot
  history. ⚠️ Re-confirm the CDN URL still 200s after ~24h before full rollout; if it
  ever 404s, history-rot risk returns and we'd need a pinned core + CDN fallback.
- **Rendering rule:** an emoji renders in a message iff it was in the posting bot's own
  store at post time; durable thereafter.
- **Upload latency is small and NOT rate-limited.** 20 cold uploads: ~5.1s sequential,
  ~3.0s at concurrency 10, **zero 429s** (bucket limit=15, resets ~67ms; server-side
  ~7/s ceiling). One-time per item, ever.
- **Discord emoji-name rules (verified):** regex `^[A-Za-z0-9_]{2,32}$`, case-sensitive,
  **unique per store** (dupes → 400), non-ASCII rejected.

## Decisions

- **Pure lazy, NO eager loading** (simplicity). Upload on first sight; never pre-warm.
- **Hashless emoji names** (readability). Collision gate below justifies dropping the
  hash suffix.
- Working set expected **< 1000** concurrent → 2000-slot store rarely evicts; LRU is a
  dormant safety net, not a hot path.

## Collision gate — PASSED ✅

`emoji_name()` spec (hashless):
```
NFKD -> encode ascii ignore -> lower
re.sub(r'[^a-z0-9]+', '_') ; strip('_')
[:32] ; rstrip('_')
pad to >= 2 chars ('item' if empty)
```
Checked over **3,796** distinct Legendary+Exotic weapon (itemType 3) + armor (itemType 2,
subtypes 26–30) display names in the frozen manifest:
- **0 collisions** (no two distinct item names map to the same emoji name).
- Only **5** names truncate at 32 chars (the "…of the Emperor's Champion" set +
  "Hawthorne's Field-Forged Shotgun"); none collide.
- Manifest is frozen (no more Destiny updates) → 0 today = 0 forever. Hashless is safe.

## Build

1. **`emoji_name(item_name: str) -> str`** — the hashless slug function above. Pure,
   deterministic. Location TBD (Fable to advise — likely `dd/common/utils.py`).
2. **`EmojiStore`** — pure-lazy per-bot cache:
   - Persistent table `emoji_cache(bot_scope, item_name PK, emoji_name, emoji_id,
     last_used)` in `dd/common/schemas.py` (mirror `RotationData` idioms:
     `@ensure_session(db_session)`, MySQL upsert, `updated_at`-style stamp). One Atlas
     migration.
   - `resolve(item) -> str`: cache hit → `<:emoji_name:id>`. Miss → fetch Bungie icon,
     `create_application_emoji`, insert row, return markup. All under an async lock to
     serialize uploads (Discord serializes store writes anyway).
   - LRU eviction when store ≥ ~1900: delete least-recently-used app emoji + row (safe,
     per CDN-persistence finding).
   - **Warn-path** (3 failure points, each a clear warning + graceful fallback, never a
     silent broken `:name:`):
     1. no icon / icon fetch ≠ 200 → warn, fall back to plain item text.
     2. unknown item (name not in manifest) → warn caller.
     3. name collision / Discord 400 dup → warn, disambiguate, retry.
3. **Utility method: Destiny item → emoji string.** Fable advises placement (on the item
   type vs a free function vs the store). Wraps `emoji_name` + `EmojiStore.resolve`.
4. **Fable (claude-fable-5) as architecture advisor** for store placement, the utility
   method's home, and the lock/eviction shape — before writing the final code.

## Follow-ups / risks

- Re-confirm CDN-persistence after 24h (see warning above).
- Cross-type name collision (a weapon and an armor piece sharing a display name → one
  shared emoji/icon): none in current data by the 0-collision result, but if it ever
  matters, key the slug on `f"{name}|{itemType}"`.
- Which bot owns which store: only the posting bot renders; ensure the bot that posts
  item content owns the emoji it uses.

## When complete

Remove this file from `plans/` (per repo convention), prompting the user if only
partially executed.
