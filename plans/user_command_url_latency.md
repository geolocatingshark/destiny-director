# Evaluate eager URL-following latency in type-1 user commands

> **Precondition:** deferred performance pass. Pre-existing behaviour carried over from v2
> (same under the old `str.format` substitution); surfaced during the lightbulb-v3 review.
> Re-verify the code below (grep by symbol name, not line number) before implementing.

**File:** `dd/beacon/extensions/user_commands.py` (the `response_type == 1` / Text handler
in `_user_command_response_func_builder`)

## Problem

Before responding, the handler awaits `follow_link_single_step` for *every* URL found in
the response text (to resolve redirects). `follow_link_single_step` (`dd/common/utils.py`)
retries up to ~10 times with ~10s sleeps on `>= 400` responses, so a single dead/slow link
in a command's response can block the handler for up to ~100s. The command defers first,
but a Discord interaction token only lives ~15 minutes and the followup can still feel
broken to the user; worse, every invocation pays the redirect-resolution cost synchronously.

## Possible directions (to evaluate, not yet decided)

- Cache resolved redirect targets (per URL, with a TTL) so repeat invocations don't re-fetch.
- Resolve links concurrently (`asyncio.gather`) instead of sequentially.
- Bound the per-link work (lower retry count / total timeout) for this path, or fall back to
  the original URL on timeout instead of blocking.
- Resolve redirects once at command *add/edit* time and store the resolved URL, rather than
  on every invocation.

Follow repo rules in `CLAUDE.md` (uv, ruff line-length 88 + double quotes, ty, async
throughout). Never deploy to prod.
