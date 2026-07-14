# Legacy / world-activity rotation — deferred cleanups

Low-priority follow-ups surfaced by the `dev`-vs-`main` code review (2026-07-14). None
are correctness bugs (those were fixed on `fix/legacy-rotation-review-fixes`); these are
reuse / simplification / altitude items deliberately deferred. Pick off individually.
Remove this file once all are done (or prune items as they land).

## Reuse / dedup

1. **Weapon-type slug list duplicated.** `dd/common/legacy_activities.py`
   (`_WEAPON_TYPE_SLUGS`) and `dd/anchor/extensions/portal_ops.py` (`_WEAPON_TYPE_EMOJI`)
   carry the same 17 weapon-type slugs plus the same `bow → combat_bow` fallback — the
   comment in `legacy_activities.py` already admits it's "the same set matched in
   portal_ops". Hoist one shared tuple/frozenset into `dd.common` and import it in both.
   Note the two match *differently* now (portal_ops matches Bungie's structured
   `itemTypeDisplayName` exactly; legacy_activities matches the `(Type)` hint) — share the
   vocabulary, not the matching logic.

2. **`_field_label` duplicated.** `dd/common/legacy_activities.py:_field_label` is
   identical to `dd/common/rotation_schema.py:_legacy_field_label`
   (`name.replace("_", " ").title()`) — and `legacy_activities` already imports
   `rotation_schema`. Collapse to one shared helper.

3. **`(Type)`-stripping / type-hint parsing duplicated.** `_plain_name` +
   `type_hint` extraction in `dd/anchor/extensions/bungie_api/item_index.py`,
   `_linked` (name strip) and the new `_weapon_type_hint` in
   `dd/common/legacy_activities.py` all parse the stored `Name (Type)` value shape. Share
   one `plain_name` / `type_hint` pair so a format change (e.g. `Name [Type]`) only edits
   one place.

## Simplification

4. **`render_dares_sections` inlines the armor emoji.** It builds `f":armor: {a}"`
   directly instead of calling the `_armorize` helper it sits next to
   (`dd/common/legacy_activities.py`). Use `_armorize(a)` for consistency.

5. **`build_pages` / `build_week_pages` are needlessly `async`.**
   `dd/beacon/extensions/legacy_activities.py` — both are `async def` but contain no
   `await` (pure render loops). Making them plain `def` drops the misleading coroutine
   wrapper; update the call sites/tests. (Low value — touches several call sites.)

## Altitude / cosmetic

6. **user_commands clash self-heal has no recovery path.**
   `dd/beacon/extensions/user_commands.py` now *deletes* a DB-backed command row on a
   name clash with a code-defined command (intentional self-heal). There's no
   rename/rollback path if that was an operator-authored command. Consider logging the
   deleted definition (or a soft-delete) so it's recoverable.

7. **`render_upcoming_sections` weekly range is one day late.**
   `dd/common/legacy_activities.py` — the weekly `when()` renders the range end as
   `day + step` (the *next* reset day) rather than `day + step - 1 day`. Cosmetic; the
   displayed end date reads one day past the actual week.
