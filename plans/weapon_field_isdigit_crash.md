# Weapon field — a superscript digit 500s the form (LOW PRIORITY)

**Status:** deferred / filed. Found 2026-08-05, while auditing the emoji-id checks for
`plans/preview_renderer_unification.md`.

## Problem

`resolve_weapon` (`dd/anchor/hybrid_post_core.py`) decides whether a typed weapon field
is a manifest hash or a free-typed name by asking `str.isdigit()`, then calls `int()`:

```python
if value.isdigit():
    wanted = int(value)
```

Those two do not agree. `isdigit()` is true for anything with Unicode's *Digit*
property, which includes superscripts and enclosed forms; `int()` only accepts *decimal*
digits. So the gap is exactly `isdigit() and not isdecimal()` — `²`, `³`, `¹`, `①`, `⑵`
and friends — and every one of them raises:

```
>>> resolve_weapon("²", [])
ValueError: invalid literal for int() with base 10: '²'
```

It is reachable from the web forms: the weekly-reset and trials weapon slots accept a
free-typed name (that is the point of the fallback), so an owner who types or pastes a
lone `²` into one gets a 500 out of save or preview instead of a weapon named `²`.

Note `١٢٣` (Arabic-Indic) does *not* crash — `int()` takes it — which is why a naive
"non-ASCII digits" reading of the bug misses half of it.

## Why it's not urgent

- Owner-only admin form, and the input is deliberate: nobody types `²` as a weapon name
  by accident. No data loss, no bad post — just a 500 on the request.
- Long-standing; predates the preview work entirely.

## Fix

`value.isascii() and value.isdigit()`, matching what the emoji-id checks now use
(`cv2_render._cdn_emoji_substituter`, `hybrid_post_core._html_emoji_substituter`) — a
manifest hash is ASCII digits, and the narrow reading is the one the rest of the code
already takes.

`isdecimal()` would also stop the crash, but it keeps `١٢٣` routing to a hash lookup,
which is not what someone typing Arabic-Indic digits into a *name* field means.

Add a test beside the existing `resolve_weapon` cases asserting `²` resolves to a
hash-less `WeaponRef`, not an exception — the crash is the interesting case, and it is
one line to pin.

## Where

- `dd/anchor/hybrid_post_core.py` — `resolve_weapon`.
- Callers, for the reachability claim: `dd/anchor/extensions/weekly_reset.py`
  (`resolve_reward_value`), `dd/anchor/extensions/trials.py` (three sites),
  `hybrid_post_core.resolve_weapon_lines`.
