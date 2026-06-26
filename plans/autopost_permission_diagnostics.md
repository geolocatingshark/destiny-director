# Plan: `/autopost` — proactive bot-permission gate + permission diagnostics

> **Status:** approved 2026-06-26, not yet implemented. Re-verify symbols + line numbers
> against the current tree (grep by name) before executing — this repo shifts under you.

## Context

`/autopost <type> enable` (in `dd/beacon/extensions/autoposts.py`) lets a server admin
subscribe a channel to an autopost feed. Today it only checks the **invoker's** perms and
that the bot can *see* the channel; whether the **bot can actually post** is discovered
reactively — the legacy path even validates by posting a throwaway `"Test message :)"` and
deleting it. If the bot lacks **Send Messages**, the autopost is enabled anyway and then
**silently fails** later, with no signal to the user.

This change makes the bot **refuse to enable an autopost unless it has Send Messages** in
the target channel (computed *proactively*), surfaces that as a named **permission error**,
and shows end users a **permission diagnostics** embed: a ✅/❌ checklist of the perms the
bot needs, **what specifically is blocking** each missing one (a channel override on the
bot / a role / @everyone, vs. not granted at the server level), and always a bot-owner
contact for help.

### Decisions (confirmed with user)
- **Diagnostics show only when an enable is blocked** (no standalone `/autopost … diagnose`).
- **Permissions-focused**: ✅/❌ per required/advisory perm, **plus the specific blocking
  source where determinable**, **plus** owner contacts (already via `set_owners_footer`).
- Hard gate is **Send Messages** (+ View Channel, and Send-in-Threads for threads). Embed
  Links / Manage Webhooks are advisory (shown, not gated).
- **Pick the follow method proactively by channel type**: only standard **text channels**
  (`GUILD_TEXT`) use Discord's webhook-follow; threads, forums, media, voice/stage and
  announcement targets go straight to the **legacy** (bot-posts) mirror — keeping the
  existing reactive `NEEDS_LEGACY` catch as a safety net.

## Approach

### 1. Bot-permission helpers — `dd/beacon/utils.py`
Mirror the existing `check_invoker_has_perms` (`utils.py:31-64`) but for the **bot's own**
member, and expose the data the diagnostics need.

- `async def compute_bot_perms(ctx) -> hikari.Permissions | None`: resolve the bot member
  (`me = ctx.client.app.get_me()`; `bot.cache.get_member(guild_id, me.id) or await
  bot.rest.fetch_member(...)`), resolve the channel (thread → parent, must be
  `PermissibleGuildChannel` — same guards as the invoker helper), then
  `calculate_permissions(member, channel)` (`toolbox.members`). Return `None` when
  undeterminable (no guild, `get_me()` None, non-permissible channel, or
  `CacheFailureError` from toolbox) — callers treat `None` as "can't post / can't tell"
  and render the diagnostics with a "couldn't read my permissions here" note.
- `def explain_missing_permission(member, channel, permission) -> str | None`: **best-effort
  block-source attribution.** Replicate the exact chain in
  `toolbox/members.py:calculate_permissions` (87-154): @everyone base + member-role perms →
  admin/owner → @everyone channel overwrite → aggregated member-role channel overwrites →
  member-specific overwrite. For a permission that ends up **missing**, return the
  *most-specific* cause:
  - member-specific overwrite denies it → "a channel permission override **on me** denies it"
  - a role's channel overwrite denies it (and not re-allowed more specifically) → "a channel
    override on the **@\<role\>** role denies it" (name the role(s))
  - @everyone channel overwrite denies it → "the channel's **@everyone** override denies it"
  - never granted by any of the bot's roles at guild level → "none of my roles grant it
    here — grant my role this permission or add a channel override"
  Return `None` if the permission is actually present. Pure given member+channel+overwrites
  (cache data) ⇒ unit-testable; if inputs are unavailable, the caller falls back to a plain
  ✅/❌ without the source line.

The gate resolves member+channel **once** and reuses them for both `calculate_permissions`
and `explain_missing_permission` (don't fetch twice).

### 2. Required-vs-advisory perms table — `autoposts.py`
A single source of truth near `end_user_allowed_perms` (line 34): a small frozen dataclass
`(permission, label, required, why)` and a list — **View Channel** (required), **Send
Messages** (required), **Embed Links** (advisory — embeds won't render), **Manage
Webhooks** (advisory — enables the webhook-follow delivery path). A `for_channel(channel)`
helper appends **Send Messages in Threads** (required) when the dest is a `GuildThreadChannel`.

### 3. Preflight 3 — the gate (`FollowControl.invoke`, autoposts.py:301-355)
Insert **after** Preflight 2 (invoker perms, ends line 330) and **before** the
`if enabling:` apply block (line 335), and **only when enabling** (never block `disable` —
users must be able to turn a broken autopost off):
- Compute the bot's perms once. If **any required perm is missing** (or perms are `None`),
  respond with the **diagnostics embed** (§5) and `return` — do **not** add the mirror.
- Gate applies to **both** delivery paths (webhook and legacy): the non-legacy path falls
  back to legacy (`_enable_autopost` 258-263) and `ping_role` forces legacy, both of which
  post as the bot, so Send Messages is the correct universal requirement. Manage Webhooks
  stays advisory (the bot degrades to legacy), so it never blocks.

### 4. Remove the `"Test message :)"` probe — `enable_legacy_mirror` (autoposts.py:136)
The proactive gate replaces it. Delete the `await (await channel.send(...)).delete()` line
(keep the `TextableChannel` guard and the `add_mirror(..., legacy=True)`). This removes the
user-visible ghost message + a round-trip; real send failures are still caught by the
existing reactive `MISSING_PERMS` handler.

### 4b. Proactive follow-method selection by channel type — `_enable_autopost` (240-263)
Discord's channel-follow creates a follower **webhook in the target**, which only standard
**text channels** (`GUILD_TEXT`, type 0) support; thread (10/11/12), `GUILD_FORUM` (15),
`GUILD_MEDIA` (16), voice/stage (2/13), and `GUILD_NEWS`-as-target (5) all return `50024`
("Cannot execute action on this channel type") — confirmed against hikari's `ChannelType`
and Discord's follow docs. Today the code discovers this **reactively** (try webhook → catch
`NEEDS_LEGACY` → legacy). Make it **proactive** using the channel **already fetched for the
perm gate** (§3), so non-text targets never make a doomed `follow_channel` call:
- `_WEBHOOK_FOLLOW_TARGET_TYPES = frozenset({h.ChannelType.GUILD_TEXT})`. Decide on the
  **target's own type** (the thread itself, not its resolved parent).
- New `_enable_autopost` order: `ping_role` → legacy (+ `_drop_existing_follow`, as today);
  **else if the target's type ∉ the allowlist → legacy directly** (no follow webhook to drop
  — non-text targets never had one); else try webhook-follow.
- **Keep** the `except h.BadRequestError / NEEDS_LEGACY → legacy` fallback as a safety net for
  any type Discord rejects that the allowlist didn't anticipate. Over-routing to legacy is
  safe now that §3 guarantees the bot has Send Messages.
Thread the fetched channel from the gate into `_enable_autopost` so the type is read without
a second fetch.

### 5. Diagnostics embed + the named permission error — `autoposts.py`
- Add `MirrorOutcome.BOT_MISSING_SEND` to the enum (77-83) — the **named permission error**
  (proactive, so *not* added to `_OUTCOME_BY_CODE`). User-facing embed title: **"Permission
  Error"**.
- New builder `permission_error_embed(bot_owners, statuses, perms_known: bool)` (pure, given
  precomputed per-perm status): renders each perm as `✅/❌ Label (recommended)?` and, for
  missing required perms, a `   └ <block-source or why>` line; `cfg.embed_error_color`;
  `set_owners_footer(embed, bot_owners)` for the always-on owner contacts; when
  `perms_known` is False, add a "I couldn't read my own permissions here — am I fully in
  this server?" note.
- **Supersede the static `bot_missing_permissions_embed` (59-74)** and **upgrade
  `respond_missing_perms`** (104-108) to compute perms + delegate to the new embed, so all
  three "bot lacks perms" paths converge on one diagnostic: Preflight 1 (fetch 403 ⇒ no
  View Channel ⇒ perms `None`), Preflight 3 (proactive), and the late reactive
  `MISSING_PERMS` catch (342-344). Delete `bot_missing_permissions_embed` after confirming
  (`rg`) it has no other callers.

### 6. Tests
Mirror existing styles (`test_autoposts_classifier.py` enum asserts; `MagicMock(spec=…)` +
`monkeypatch` like `test_ignore_non_src_channels.py`). The perms table, the explainer, and
the embed are **pure** — no DB.
- `dd/beacon/tests/test_autopost_perms.py` *(new)*: required set; thread adds Send-in-Threads;
  `explain_missing_permission` attribution (member-overwrite deny / role-overwrite deny names
  the role / @everyone-overwrite deny / not-granted-at-guild) with stub member+channel+
  overwrites; `permission_error_embed` renders ❌ Send Messages + a block line + ✅ others +
  owner footer; `perms_known=False` note.
- `dd/beacon/tests/test_bot_perms_helper.py` *(new)*: `compute_bot_perms` plumbing — no guild
  → None, `get_me()` None → None, thread→parent resolution, cache→REST member fallback,
  `CacheFailureError` → None, non-permissible channel → None (patch
  `dd.beacon.utils.calculate_permissions`).
- Extend `test_autoposts_classifier.py`: `MirrorOutcome.BOT_MISSING_SEND` exists; existing
  4 tests unchanged.
- Follow-method decision (pure): `_supports_webhook_follow` → `True` only for `GUILD_TEXT`;
  `False` for thread/forum/media/voice/stage/news (stub `channel.type`).

## Critical files
- `dd/beacon/utils.py` — `compute_bot_perms`, `explain_missing_permission` (mirror
  `check_invoker_has_perms`; chain from `toolbox/members.py:87-154`).
- `dd/beacon/extensions/autoposts.py` — perms table, Preflight 3 gate, `permission_error_embed`,
  remove the test-send, `MirrorOutcome.BOT_MISSING_SEND`, upgrade `respond_missing_perms`,
  delete static embed.
- `dd/beacon/tests/test_autopost_perms.py` *(new)*, `test_bot_perms_helper.py` *(new)*,
  `test_autoposts_classifier.py` (extend).
- Reference (reuse, no edits): `dd/beacon/utils.py:31-64` (template), `toolbox/members.py`
  (canonical perm chain), `dd/common/cfg.py` (`embed_error_color`), `dd/common/bot.py`
  (`fetch_owners`, `get_me`).

## Verification (dev only — NEVER prod)
- `uv run ruff check`, `uv run ty check`, `uv run python -m pytest` (sandbox disabled).
- `make deploy-beacon-dev` (sandbox disabled).
- In a dev test guild, on a channel where the bot has perms: `/autopost <type> enable`
  succeeds (and **no** "Test message" appears anymore). Then add a **channel override
  denying Send Messages** to the bot's role (or @everyone) and re-run → expect the
  **"Permission Error"** embed: ❌ Send Messages with `└ … @everyone/@role override denies
  it`, ✅ View Channel, advisory items shown, owner contact in the footer, and **no mirror
  written** (DB unchanged). Also test: remove **View Channel** → diagnostics render with the
  "couldn't read my permissions" note. Confirm **`disable` still works** when the bot lacks
  Send Messages. Restore perms → enable works.
- **Follow method:** enabling in a **text** channel uses webhook-follow; enabling in a
  **forum/thread/announcement** channel routes straight to **legacy** with **no
  `follow_channel` call / no `50024` in the logs** (proactive), and still posts correctly.
- Commit as one revertable unit on the user's go-ahead; don't push/prod unless asked
  (beware: this repo's tree shifts mid-session — re-check `git status`, never blind
  `git stash pop`).

## Risks / notes
- **Block-source attribution is best-effort.** It depends on cache/fetch of guild roles +
  member + channel overwrites; when unavailable, degrade to a plain ✅/❌ + the static `why`
  (no source line) and the "couldn't read my permissions" note — never error the command.
- Keep the gate **enable-only**; never block `disable`.
- `compute_bot_perms` reads cache first, REST fallback — a freshly-joined guild may need the
  REST member fetch.
