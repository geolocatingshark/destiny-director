# Plan: Discord OAuth for the Anchor web UI

## Context

The `dd.anchor` bot serves two web editors (rotation editor, weekly-reset form) from a single
aiohttp app (`dd/anchor/web.py`). Today they are guarded by a **hand-rolled magic-link scheme**:
an owner-only slash command (`/rotation edit`, `/weekly_reset create`) mints an HMAC-signed token,
delivered as a link/button; the first page load trades `?token=` for a signed cookie. The scheme is
**duplicated** across `rotation_editor.py` and `weekly_reset.py`, applied **per-handler** (no
middleware), and — critically — it only proves *"someone received the link"*, **not** *which Discord
user* is visiting. Anyone who obtains a link (forward, log leak, shoulder-surf) is in for 2 hours.

**Goal:** replace link/token/cookie auth with **Discord OAuth** as the sole gate for *all* web
access, admitting **only bot owners / team members** — exactly the set returned by
`CachedFetchBot.fetch_owner_ids()`. Outcome: every visitor proves a specific Discord identity, and
authorization is re-derived from the live owner list rather than possession of a secret.

**Verdict: feasible and recommended.** The Bungie OAuth code
(`dd/anchor/extensions/bungie_api/oauth.py`) is a near-exact template (state manager, `yarl`
authorize URL, `ClientSession().post(TOKEN_URL, data=...)` exchange). No DB/schema change is needed —
sessions stay stateless signed cookies; the owner list is the already-cached REST result of
`fetch_owner_ids()`. The only genuinely new primitive is one aiohttp middleware.

**Decisions taken with the user:**
- Keep `/rotation edit` and `/weekly_reset create` as **bare-URL launchers** (still owner-gated in
  Discord; button now points at the plain editor URL, OAuth handles the rest).
- Add a **dev-only auth bypass**, gated so it is honored **only when `cfg.test_env` is truthy**
  (`TEST_ENV` set) — never in prod.

Work happens on a worktree branched from `dev` (this worktree already is; branch off `dev` if starting fresh).

---

## Design

### New module: `dd/anchor/extensions/web_auth.py` (a lightbulb extension)

An extension (not a bare helper) because it needs the three things `weekly_reset.py` already does:
`loader = lb.Loader()` for auto-discovery/load on `StartingEvent`; import-time
`web.register_routes(...)` calls; and a `@loader.listener(h.StartedEvent)` that stashes the live bot
into a module global `_bot: CachedFetchBot | None` (mirror `weekly_reset.py:1374` + `:1751-1760`) so
the middleware can call `fetch_owner_ids()`. Keeps the two feature modules free of all auth code.

Contents:

1. **Constants:** `_SESSION_COOKIE = "dd_auth"`, `_SESSION_TTL = 24h`, `_STATE_TTL = 5min`, and
   Discord endpoints `AUTHORIZE_URL`/`TOKEN_URL`/`USER_URL` under `https://discord.com/api`.
   Single callback path constant `_CALLBACK_PATH = "/auth/callback"` (derive `redirect_uri` from
   `cfg.public_base_url + _CALLBACK_PATH` in **one** place — exact-match is the #1 failure mode).

2. **Session cookie (stateless HMAC).** Reuse the proven scheme from `rotation_editor.py:72-150`,
   extended to carry the user id. Payload `"<user_id>.<expiry_epoch>.<hex_hmac>"`,
   `hmac = HMAC-SHA256(key, f"{user_id}.{expiry_epoch}")`. Key derivation
   `sha256(b"anchor-web-auth-session|" + cfg.discord_token_anchor.encode()).digest()` — same
   "derive from bot token, distinct salt" convention (no new signing secret). Helpers
   `mint_session(user_id)`, `resolve_session(cookie) -> int | None`, `set_session_cookie`,
   `clear_session_cookie`. Verify inside `try/except (ValueError, TypeError)` that **fails closed**
   (copy the hardening from `rotation_editor.py:114-120`). Cookie flags: `httponly=True`,
   `secure=cfg.public_base_url.startswith("https")`, `samesite="Lax"` (needed so the cookie survives
   the cross-site top-level redirect back from discord.com), **`path="/"`** (one cookie for the whole
   app — replaces the old per-surface `/rotation` `/weekly_reset` paths), `max_age=TTL`.

3. **State manager `_AuthStateManager`.** Mirror `OAuthStateManager` (oauth.py:25-91) — in-memory
   `dict`, uuid4 codes, 5-min TTL, single-use `consume`, proactive sweep — but map
   `state -> (expiry, next_path)` so the post-login redirect target is server-held (never trusted
   from the callback query). Own instance, not shared with Bungie's, to keep surfaces isolated.

4. **Routes (`register_auth_routes(app)`):**
   - `GET /auth/login` — read `?next`, **sanitize** (accept only a single-leading-`/` internal path,
     reject `//…` and absolute URLs → default `/`; this open-redirect guard is the one
     security-critical validation, unit-tested explicitly). Mint state, build authorize URL via
     `yarl.URL(AUTHORIZE_URL).with_query(client_id=…, redirect_uri=…, response_type="code",
     scope="identify", state=…)`. `302` to it. If `public_base_url` or client id/secret empty →
     clear plain-text error (same philosophy as the current `public_base_url` guard).
   - `GET /auth/callback` — handle `?error=` (deny → 4xx). `consume(state)` → `next_path`
     (unknown/expired/reused → 400; single-use consume is the CSRF defense). Exchange code
     (`ClientSession().post(TOKEN_URL, data={client_id, client_secret, grant_type:"authorization_code",
     code, redirect_uri})`, copy oauth.py:144-156 shape; non-200/`KeyError` → 502). `GET USER_URL`
     with `Authorization: Bearer …` → `user["id"]`. **Verify:**
     `int(user_id) in await _bot.fetch_owner_ids()` else `403`. Mint session, `302` to `next_path`,
     set cookie. **Do not persist** the Discord token — `identify` was a one-shot to learn the id.
   - `POST /auth/logout` (GET also acceptable) — clear cookie (`max_age=0`, `path="/"`), `302`
     to `/auth/login`.
   - Error responses: short plain-text `aiohttp.web.Response`; never echo `code`/`state`/tokens
     (app already sets `access_log=None`, web.py:76-80).

5. **Enforcement middleware (`register_auth_middleware(app)` → `app.middlewares.append(...)`).**
   Registrars receive the app and run in `web.start()` before `runner.setup()`, so appending a
   middleware there is safe — **no change to `web.py` needed**. `_auth_middleware(request, handler)`:
   - **Allowlist bypass** for paths starting with `("/auth/", "/oauth/callback", "/static/")` —
     the login flow, the *Bungie* callback (self-guarded by its own state code, must stay reachable),
     and static assets.
   - **Dev bypass** (see below): if active, treat request as authenticated owner.
   - Resolve `dd_auth` cookie → user id. Unauthenticated: `GET`/`HEAD` → `302`
     `/auth/login?next=<url-encoded path_qs>`; other methods → `401` (never redirect a POST). Valid
     cookie but `int(uid) not in await _bot.fetch_owner_ids()` → `403`. Else pass through (optionally
     stash `request["auth_user_id"]`). `_bot is None` → `503` (defensive).
   - **Origin defence (folded in):** for unsafe methods (`POST/PUT/PATCH/DELETE`), if `Origin` header
     present and `public_base_url` set, require it to match `public_base_url` (rstrip `/`) else `403`
     — the single consolidated replacement for the two deleted `_origin_ok` helpers, belt-and-suspenders
     atop `SameSite=Lax`. (Discord callback is a GET, so this never interferes.)

   Centralizing here makes "auth by default" an invariant: any future route is protected automatically.

### Dev-only bypass (gated on `cfg.test_env`)

Add `cfg.dev_auth_user_id = _getenv("DEV_AUTH_USER_ID", "")`. In the middleware, the bypass is honored
**only when both** `cfg.test_env` is truthy (i.e. `TEST_ENV` is set — the existing debug-mode
indicator, cfg.py:187) **and** `cfg.dev_auth_user_id` is non-empty. When active, requests are treated
as the configured owner id (skip cookie resolution). Double-gate guarantees it is inert in prod even
if the env var leaks into a prod config. Document this loudly in code + `.env-example`.

### Config additions

`cfg.py` (after the Bungie block, ~line 274), all defaulting to `""` — **not** added to import-time
required-var validation (would break local dev / non-anchor contexts):
```python
discord_oauth_client_id = _getenv("DISCORD_OAUTH_CLIENT_ID", "")      # == the bot's application id
discord_oauth_client_secret = _getenv("DISCORD_OAUTH_CLIENT_SECRET", "")  # Portal → OAuth2 tab (NOT the bot token)
dev_auth_user_id = _getenv("DEV_AUTH_USER_ID", "")                    # honored ONLY when TEST_ENV is set
```
`.env-example`: document the three vars near the Bungie/PORT block, noting `CLIENT_ID` == application
id, the secret is from the Developer Portal OAuth2 tab, register redirect
`{PUBLIC_BASE_URL}/auth/callback`, and that `DEV_AUTH_USER_ID` only applies with `TEST_ENV` set.

**No DB/schema change.**

---

## Changes to existing files

### `dd/anchor/extensions/rotation_editor.py` — strip auth
Delete: `_signing_key` (72-81), `RotationSessionManager` (84-123), `_session_from_request` (129-130),
`_set_session_cookie` (133-150), `_origin_ok` (153-163), unused `_SESSION_*`/`_EXPIRED_MSG` constants
(61-63), and now-unused `hashlib`/`hmac` imports. In `_handle_home_get` (305-319) remove the `?token=`
exchange block and the `resolve(...)` 401 guard → it just renders the homepage. Remove the
`resolve(...)`/`_origin_ok(...)` guards from `_handle_edit_get`, `_handle_preview`, and the edit POST
(auth is now the middleware's job). Update the token/cookie docstring (16-27). Convert `/rotation edit`
(424-453): drop `mint()`, set `url = f"{cfg.public_base_url}/rotation"`; keep the
`if not cfg.public_base_url` guard + ephemeral response.

### `dd/anchor/extensions/weekly_reset.py` — strip auth (same pattern)
Delete `_signing_key` (1377-1386), `WeeklyResetSessionManager` (1389-1426), `_session_from_request`
(1429-1430), `_set_session_cookie` (1433-1446), `_origin_ok` (1449-1458), `_authed` (1461-1462), all
per-handler call sites, the `?token=` exchange in the home GET, unused constants/imports. Convert
`/weekly_reset create` (1713-1748): drop `mint()`, LinkButton → `f"{cfg.public_base_url}/weekly_reset"`;
keep the `public_base_url` guard.

### `dd/anchor/web.py`, `dd/common/bot.py`, `dd/common/auth.py`
**No changes** — reference only (`register_routes`/`app.middlewares`, `fetch_owner_ids`, command-side
gate stays as-is).

---

## Tests (`dd/anchor/tests/`, pytest-asyncio, mock the network)

Reuse the `_FakeRequest` harness from `test_rotation_editor.py` (fake `cookies=`/`query=`/`body=`,
call handlers directly — no live server).

**New `test_web_auth.py`:**
- Session primitive: `mint`/`resolve` round-trip returns the right user id; rejects empty/garbage/
  tampered-id/tampered-expiry/forged-sig; hand-signed expired token → None.
- Middleware (monkeypatch `_bot` to a stub whose async `fetch_owner_ids()` returns `[123]`): allowlist
  paths pass without a cookie; unauth `GET /rotation` → 302 to `/auth/login?next=/rotation`; unauth
  `POST /rotation/edit` → 401; owner cookie → handler runs; non-owner cookie (user 999) → 403;
  origin mismatch on POST → 403; `_bot is None` → 503; **dev bypass**: with `cfg.test_env` +
  `dev_auth_user_id` monkeypatched, unauth request passes as owner; with `test_env` falsy it does not.
- OAuth endpoints (patch outbound `ClientSession` to canned token/user JSON — no network, CI-safe):
  `/auth/login` → 302 to a `discord.com/.../authorize` URL carrying `client_id`, `scope=identify`,
  `state`, `redirect_uri`; empty `public_base_url` → error; `next` sanitization (`//evil.com`,
  `https://evil.com` → `/`; `/weekly_reset` preserved); callback happy path (owner) → 302 to `next` +
  `dd_auth` cookie (HttpOnly/Lax/Path=/); unknown/expired/reused state → 400; non-owner id → 403 no
  cookie; `?error=access_denied` → 4xx; logout clears cookie + redirects.

**Modify** `test_rotation_editor.py` / `test_weekly_reset.py`: remove tests asserting old token/cookie
behavior (token→cookie exchange, `*_without_cookie_is_401`, SessionManager mint/resolve/expiry) and
per-handler 401 assertions; drop `cookies=_cookies(token)` plumbing — handlers now assume an
authenticated request.

---

## Ordered implementation steps
1. `cfg.py` + `.env-example` — add the three env vars (default `""`), document them.
2. `web_auth.py` — new extension: constants, session helpers, `_AuthStateManager`, `next` sanitizer,
   three route handlers, `register_auth_routes`, `_auth_middleware` (+ dev bypass + origin check) and
   `register_auth_middleware`, `loader`, `StartedEvent` bot-stash, import-time `register_routes` calls.
3. `rotation_editor.py` — strip auth, convert launcher, fix imports/docstring.
4. `weekly_reset.py` — same.
5. Tests — add `test_web_auth.py`; prune/adjust the two feature test files.
6. Out-of-band: register `{PUBLIC_BASE_URL}/auth/callback` in the Discord Developer Portal (OAuth2 →
   Redirects); set `DISCORD_OAUTH_CLIENT_ID`/`SECRET` on Railway.

---

## Verification

- **Static/CI:** `make check` (ruff → ty → pytest, SQLite). New unit tests must pass with **no
  network** (outbound `ClientSession` mocked). Confirm no orphaned imports/constants remain in the two
  stripped modules (ruff F401 + ty catch these).
- **Local end-to-end (dev bypass path):** set `TEST_ENV` + `DEV_AUTH_USER_ID=<your id>`, run
  `uv run python -OOm dd.anchor`, hit `http://localhost:8080/weekly_reset` — should load directly
  (bypass), and `/rotation`. Unset the bypass → GET should 302 to `/auth/login` (which errors without
  `public_base_url`, proving the gate is closed).
- **Local end-to-end (real OAuth):** run a tunnel (cloudflared/ngrok) to `cfg.port`, set
  `PUBLIC_BASE_URL=https://<tunnel-host>`, register `https://<tunnel-host>/auth/callback` in the
  Portal, set client id/secret. Visit `/weekly_reset` → bounced through Discord consent → back,
  authenticated. Verify a **non-owner** Discord account gets 403. Verify `/auth/logout` forces re-login.
- **Regression:** confirm the Bungie `/bungie login` flow (its `/oauth/callback`) still works — it is
  on the middleware allowlist and unchanged.

## Risks / flags
- **Redirect-URI exact match** (scheme/host/path, no trailing slash) across `/auth/login`, the token
  exchange, and the Portal — derive all from one `public_base_url + _CALLBACK_PATH`.
- **Open-redirect via `next`** — the sanitizer is security-critical; explicit unit tests.
- **Dev-bypass fencing** — double-gate on `cfg.test_env` **and** `dev_auth_user_id`; verify inert when
  `TEST_ENV` unset. Never ships active to prod.
- **Deletion blast radius** — the two SessionManagers are per-surface; nothing outside those modules
  imports them, so removal is contained; lint/tests catch orphans.
- Keep scope minimal: `identify` only (no `guilds`/`email`); do not persist Discord tokens.
