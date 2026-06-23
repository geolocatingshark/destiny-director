# Plan: `/distortion` command (Destiny 2 hourly Distortions)

## Context

Destiny 2's **Distortions** activity makes one destination "distorted" each hour,
cycling through 7 destinations on a 7-hour loop. Bungie does **not** expose the
active distortion as a clean API field — community trackers compute it from a known
Unix-timestamp cycle. We want a lightweight `/distortion` slash command on the
**beacon** bot that tells users which destination is distorted right now, what's up
next, and how long until it rotates. This is pure computation (no Bungie API/manifest
call), so it fits the same shape as `/source_code` and `/weekly reset`.

Decisions (confirmed with user):
- **Beacon, registered globally** (no `guilds=`), like other user-facing info commands.
- Output: **current + next + countdown.**
- Include a small **footnote caveat** that the rotation is computed from a known cycle
  and may drift if Bungie realigns it.

## Rotation facts (verified)

- Order (index 0→6): `Cosmodrome → European Dead Zone → Dreaming City →
  Savathun's Throne World → Moon → Europa → Nessus`, then repeats.
- Idiomatic reference anchor: **`2026-06-14 08:00:00 UTC` = start of a Cosmodrome
  hour** (index 0). Then `index = (hours_since_reference) % 7`.
  - Derived from the community tracker's sample (`1781446950` → Nessus) and the
    `HOURS_OFFSET = 4` calibration; re-expressed as a clean `REFERENCE_DATE` so we
    don't carry a magic offset. Verified: ref+0h→Cosmodrome, +6h→Nessus, and current
    time→Nessus, all matching the tracker.
  - Source/derivation reference: <https://github.com/MelecaZane/d2-distortions>
    (`index.html` — `zones` array + `HOURS_OFFSET = 4`).

## Approach

Add one new self-contained extension. No changes needed to `__main__.py` or the
loader — `dd/common/extension_loader.py::load_extensions_strict()` auto-discovers any
non-`_`-prefixed module in `dd/beacon/extensions/` that exposes a module-level
`loader = lb.Loader()`.

### New file: `dd/beacon/extensions/distortion.py`

Follow the `dd/beacon/extensions/weekly_reset.py` idiom for the time anchor and the
`dd/common/source.py` / `dd/beacon/extensions/source.py` idiom for a plain-text slash
command.

Structure:

1. **Constants**
   - `DISTORTION_DESTINATIONS: tuple[str, ...]` — the 7 names in order above.
   - `REFERENCE_DATE = dt.datetime(2026, 6, 14, 8, tzinfo=dt.UTC)` (Cosmodrome start),
     matching the `REFERENCE_DATE` pattern in `weekly_reset.py:26` / `gunsmith.py`.

2. **Pure helper** (unit-testable, no Discord/clock dependency):
   ```python
   def distortion_at(now: dt.datetime) -> tuple[str, str, dt.timedelta]:
       """Return (current_destination, next_destination, time_until_rotation)."""
   ```
   - `hours = int((now - REFERENCE_DATE).total_seconds()) // 3600`
   - `idx = hours % len(DISTORTION_DESTINATIONS)`
   - next index = `(idx + 1) % len(...)`
   - time-until = next hour boundary relative to `REFERENCE_DATE` minus `now`.
   - Keep it tz-aware (UTC) and integer-floor based so it matches the verified formula.
   - Consider reusing `dd/sector_accounting/utils.py::EntityRotation` (a
     `list[str]` whose `__getitem__` does `index % len`). It's built for *daily*
     rotations but the modulo indexing is identical; a thin wrapper is fine, or just
     index a plain tuple — prefer the plain tuple for clarity since the countdown math
     is hourly, not daily.

3. **Command** — `lb.SlashCommand` subclass `name="distortion"`,
   `description="See which Destiny 2 destination is currently distorted"`, with
   `@lb.invoke async def invoke(self, ctx)` that:
   - computes `distortion_at(dt.datetime.now(dt.UTC))`,
   - responds with plain text (no embed needed), e.g.:
     `**Distorted now:** Nessus\n**Up next:** Cosmodrome (in 39m)`
     plus a small italic footnote: `_Rotation is computed from a known cycle; may drift if Bungie realigns it._`
   - Format the countdown as `Hh Mm` / `Mm` using simple arithmetic. `get_ordinal_suffix`
     in `dd/common/utils.py` is **not** needed here.

4. **Registration** — module-level:
   ```python
   loader = lb.Loader()
   loader.command(Distortion)   # global; no guilds= kwarg
   ```

### New test: `dd/beacon/tests/test_distortion.py`

Mirror the existing pure-logic test style in `dd/beacon/tests/test_discord_logging.py`
(plain `pytest`, no DB, no event loop needed since `distortion_at` is sync and pure —
avoids the live-MySQL hang that affects async/DB tests).

Assertions against the verified anchors:
- `distortion_at(REFERENCE_DATE)` → current `"Cosmodrome"`, next `"European Dead Zone"`,
  time-until `== timedelta(hours=1)`.
- `REFERENCE_DATE + 6h` → `"Nessus"`, next `"Cosmodrome"`.
- `REFERENCE_DATE + 7h` → wraps back to `"Cosmodrome"`.
- A mid-hour time (e.g. `+6h+21m14s`, the sample) → `"Nessus"` with countdown `38m46s`,
  confirming the floor/countdown math.
- Optionally assert the community sample timestamp `1781446950`
  (`dt.datetime.fromtimestamp(1781446950, dt.UTC)`) → `"Nessus"`.

## Files

| Action | Path |
|--------|------|
| **Add** | `dd/beacon/extensions/distortion.py` (command + pure helper + constants) |
| **Add** | `dd/beacon/tests/test_distortion.py` (pure-logic unit tests) |
| _none_ | No edits to `dd/beacon/__main__.py` or `dd/common/extension_loader.py` — auto-discovered |

## Verification

1. **Unit tests** (fast, no DB): `uv run python -m pytest dd/beacon/tests/test_distortion.py`
   — all anchor cases pass.
2. **Lint/format**: `uv run ruff check dd/beacon/extensions/distortion.py dd/beacon/tests/test_distortion.py`
   and `uv run ruff format --check ...` (line length 88, double quotes, sorted imports).
3. **Type check**: `uv run ty check` (or rely on Zed's `ty` LSP) — `distortion.py`
   should be clean with explicit annotations on `distortion_at`.
4. **Runtime smoke** (optional, needs populated `.env`): `uv run python -OOm dd.beacon`,
   confirm the bot boots and logs the `distortion` extension loaded; in a guild, run
   `/distortion` and check the output matches a community tracker
   (e.g. wewantdestiny3.com/distortion-tracker) for the current hour.

## Notes / caveats

- This is a **heuristic**, not a live API read. If Bungie ever shifts the cycle
  alignment, only `REFERENCE_DATE` (or the destination order) needs updating — keep
  that constant prominently commented with its derivation and the verification sample.
- Deliberately **no** Bungie API call, OAuth, or manifest dependency — keep it on
  beacon (public, no credentials) rather than anchor.
