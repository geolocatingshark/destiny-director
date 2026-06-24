# Bungie API: separate HTTP fetching from the domain models

> **Status:** Not started — deferred follow-on to the tier-2 `bungie_api` package split.
>
> **Precondition:** do **not** start until the hikari-lightbulb **v2→v3 migration** on
> `feature-lightbulb-v3` is merged/settled. This rewrites class internals and touches
> the live Xûr-posting path, so it should not land mid-migration.
>
> **Before implementing, re-verify by symbol name (not line number)** — grep for the
> methods named below; the package was just reorganized and may move again.

## Context

The tier-2 refactor split `dd/anchor/extensions/bungie_api.py` into a package
(`constants.py`, `manifest.py`, `oauth.py`, `models.py`, plus the `__init__.py`
extension facade). That split separated **topics into files** but left one seam
uncut: the network calls still live **inside the model classes** as classmethods, so
`models.py` imports `aiohttp`/`schemas` and the parsing logic can't be unit-tested
without faking an HTTP session.

This plan cuts that last seam — **fetching vs. parsing** — so the models become pure,
fully testable data objects.

## What moves

Today these methods both **fetch** (open an `aiohttp` session, call Bungie, check
`ErrorCode`) and **parse** (build the object). The parse halves already exist as
separate classmethods/factories — only the fetch halves need extracting:

| Method (in `models.py`) | Fetch half → `client.py` | Parse half stays in `models.py` |
|---|---|---|
| `DestinyVendor.request_from_api` | GET vendor, map `VENDOR_NOT_FOUND_ERROR_CODE` → `VendorNotFound` | `DestinyVendor.from_vendors_api_response` |
| `DestinyMembership.from_api` | GET memberships | `DestinyMembership.from_api_response` |
| `DestinyMembership.get_character_id` | GET profile | (extract a small `parse_character_id` from the response-walk) |
| `check_bungie_api_online` (already in `oauth.py`) | leave as-is or move to `client.py` | — |

## Approach

1. **New module `dd/anchor/extensions/bungie_api/client.py`** holding a thin Bungie
   HTTP client: session creation, the `X-API-Key` + `Authorization` headers (one
   place, sourced from `schemas.BungieCredentials` + the access token), and the
   `ErrorCode` → exception mapping. Functions like `fetch_vendor(...)`,
   `fetch_memberships(...)`, `fetch_profile(...)` return **raw JSON dicts**.
2. **Make the models pure.** Drop `request_from_api` / `from_api` (or keep as
   deprecated thin wrappers during transition); keep the `from_*_response` parsers.
   `models.py` should no longer import `aiohttp` or `schemas`.
3. **Update the call sites.** The real consumer is `dd/anchor/extensions/xur.py`
   (`fetch_vendor_data` and friends) plus the `AccountNumbers` command in
   `bungie_api/__init__.py` and the `__main__.py` smoke test. Change them from
   `DestinyVendor.request_from_api(...)` to `client.fetch_vendor(...)` →
   `DestinyVendor.from_vendors_api_response(...)`.
4. **Re-export** any new public surface from `__init__.py` and update `__all__`.

## Depends on / guards

- **Requires the tier-2 tests first** (`bungie_api/tests/test_models.py`,
  `test_oauth_state.py`): they pin the parsing/state behavior so this rewrite is
  verifiable. Extend `test_models.py` with a captured real vendor-response fixture to
  cover `from_vendors_api_response` end-to-end before/after the move.
- This is the only tier that can break Xûr/Eververse/Gunsmith posting (they all reach
  Bungie through `xur.py`), so verify by actually running an anchor autopost in dev
  after the change (a dev Bungie OAuth login is required first).

## Verification

1. `uv run ruff check dd` and `uv run ty check dd` clean.
2. `uv run --env-file .env python -m pytest dd/anchor/extensions/bungie_api -m "not integration"`.
3. `models.py` imports neither `aiohttp` nor `schemas`
   (`grep -nE "aiohttp|schemas" dd/anchor/extensions/bungie_api/models.py` → empty).
4. Start the anchor bot in dev, log in via `/bungie login`, and trigger a Xûr post to
   confirm the fetch→parse path still works end-to-end.
