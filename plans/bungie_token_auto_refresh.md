# Roll the Bungie refresh token forward before it expires — stub

## Status: deferred note (2026-08-05). Not scoped.

Spun out of the Bungie account page (`dd/anchor/extensions/bungie_account.py`). That page
deliberately does **not** show a token expiry date: it is not something anyone should
have to read or reason about. This records the work that would remove the expiry as a
concern rather than displaying it.

## How the token actually behaves today

Better than it first looks, and worth writing down before anyone builds a cron:

- **Every successful Bungie call already rolls the token forward.**
  `oauth.refresh_api_tokens` (the non-login path) exchanges the stored refresh token,
  and then *always* calls `BungieCredentials.set_refresh_token` with the **new** refresh
  token Bungie returns. So each vendor fetch resets the clock.
- **The stored expiry is conservative.** `set_refresh_token` writes
  `now + refresh_expires_in * 0.8` — a deliberate 20% safety factor — so the bot gives
  up on the token well before Bungie would.
- **The producers run often.** Daily 17:00 UTC: Eververse, Iron Banner, Lost Sector,
  Portal Ops. Weekly: Ada (Tue), Xûr (Fri). Any one of the Bungie-backed ones rolls the
  token.

So in normal operation the link never lapses. It lapses when the bot goes an unusually
long stretch **without any Bungie-backed fetch** — the realistic causes being an extended
outage, a long-disabled set of feeds, or a dev environment left idle. That is exactly the
case where nobody is watching a status dot either.

## What the work is

A small self-scheduled refresh, on the model of beacon's `_refresh_emoji_loop`
(`dd/common/bot.py`) — the idiom `plans/website_user_commands.md` also points at:

- On `StartedEvent`, start a loop holding a strong task reference; cancel on
  `StoppingEvent`.
- Each tick: read `BungieCredentials`; if the stored expiry is within some margin
  (a week?), call `refresh_api_tokens()` purely for its side effect of storing a fresh
  token. On failure, log — do not raise into the loop.
- Interval well under the token lifetime; daily is ample given the 0.8 factor.

## Open questions before scoping

- **Does a refresh actually extend, or does Bungie return the same refresh token?** The
  whole plan rests on the returned `refresh_token` being new. Verify against a real
  response before building — if Bungie returns the same token with a fixed absolute
  expiry, a cron cannot help and re-login is unavoidable.
- **What should happen when it can't refresh?** The link is dead and only an interactive
  login fixes it, so this wants an alert (the CRITICAL owner-ping path in
  `dd.common.discord_logging`) rather than a silent log — that is the one moment the
  expiry genuinely matters, and it should come to the operator rather than wait to be
  noticed on a page.
- Does this belong in `bungie_api.oauth` (next to the token machinery) or in the account
  page's extension (next to where its status is surfaced)? The former, probably — the
  page is a view, and the loop is not.

## If this ships

The status line on `/bungie` reduces to linked / not linked, the hover title and the
`expires` field in `GET /bungie/data` can go, and the failure mode moves from "notice a
date on a page" to "get pinged when a refresh fails" — which is the right shape for
something that fails once a quarter at most.
