# Refactor `FollowControl.invoke` autopost enable/disable to a state machine

> **Precondition:** defer until the hikari-lightbulb **v2→v3 migration** on
> `feature-lightbulb-v3` has settled — rewriting a ~200-line transactional handler
> mid-migration carries more risk than value. Re-verify the code below (grep by symbol
> name, not line number) before implementing; it will have drifted.

**File:** `dd/beacon/extensions/autoposts.py` (the `follow_control_command_maker` /
`FollowControl.invoke` body)

## Problem

The enable/disable flow currently decides what to do by matching on Discord error
*message text* — e.g. `"missing permissions" in str(e.args).lower()`,
`"cannot execute action on this channel type" in ...`, `"unknown channel" in ...`,
`"role pings are not supported by new style mirrors" in ...`. This is fragile: a
wording/locale/version change on Discord's side silently routes a channel into the wrong
mirror path or surfaces a raw traceback, and no test can pin behaviour to an external
string. The handler is also nested ~8 levels deep.

A first, low-risk pass (**done**) extracted `_is_missing_perms(e)` and
`respond_missing_perms(ctx, bot)` to dedupe the repeated permission-error response. That
removed the duplication but kept the substring matching.

## Planned deeper fix (option b)

- Classify failures *once* by exception **type + Discord error code** (`hikari` exposes
  error codes; map them) into an outcome enum, e.g.
  `MirrorOutcome.{OK, NEEDS_LEGACY, MISSING_PERMS, CHANNEL_GONE, ROLE_PING_UNSUPPORTED}`.
- Replace `_is_missing_perms`'s substring check with the code-based classifier.
- Restructure enable/disable as a flat state machine (early returns per outcome) instead
  of try/try/try nesting, with a single permission-error handler.
- Add tests over the classifier using synthetic hikari errors (type + code), so behaviour
  no longer depends on message text. Tests live under `dd/beacon/tests/` (per `CLAUDE.md`).

Follow repo rules in `CLAUDE.md` (uv, ruff line-length 88 + double quotes, ty, async
throughout). Never deploy to prod.
