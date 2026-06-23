# Components V2 migration — interactive post editor (`EmbedBuilderView`)

Status: **planning / not started** · Last updated: 2026-06-19

This is the design analysis for migrating the interactive post editor to Discord's
Components V2. It is the hardest part of the broader "embeds → Components V2"
migration; the rich read-only posts (lost_sector, xur, eververse, gunsmith) are
mechanical by comparison.

## Background: library situation

- `hikari 2.5.0` (already pinned) has **complete native Components V2 support**.
  Verified in-venv: `MessageFlag.IS_COMPONENTS_V2 = 32768`, and
  `hikari.impl.special_endpoints` exposes `ContainerComponentBuilder`
  (`accent_color`, `is_spoiler`, `add_text_display`, `add_section`/`add_component`,
  `add_media_gallery`, `add_separator`, `add_file`, `add_action_row`),
  `SectionComponentBuilder` (`accessory`, `add_text_display`),
  `TextDisplayComponentBuilder`, `ThumbnailComponentBuilder`,
  `MediaGalleryComponentBuilder`, `SeparatorComponentBuilder`.
- **No third-party library needed.** `miru 4.2` has **no** V2 layout primitives
  (still buttons/selects/modals/View only); `lightbulb v3` `components` is an
  interaction/menu framework, not a layout builder. V2 landed in hikari 2.3.0
  (Apr 2025); we're past it. Recommendation for the whole migration: a thin
  in-house adapter on `HMessage` (`to_components_v2()`), no new dependency.

## The component under migration

- File: `dd/anchor/embeds.py` — `EmbedBuilderView(InteractiveBuilderView)` +
  `build_embed_with_user(...)`.
- Consumers: `dd/anchor/extensions/posts.py` — three admin commands:
  - `/post create` → build from scratch → `channel.send(embed)`
  - `Edit` (message command) → load `message.embeds[0]` → edit → `message.edit(embed=)`
  - `Copy` (message command) → load `message.embeds[0]` → edit → `channel.send(embed=)`
- Buttons today: Edit Title / Edit Text / Edit Color / Edit Author / Edit Image /
  Edit Thumbnail / Edit Footer / Done. (Add Field / Remove Field are commented out —
  they didn't fit.)

## Why this is the crux (two load-bearing properties both break under V2)

1. **State lives in the rendered message, not the view.** Every button does
   `embed = ctx.message.embeds[0]` → mutate → `edit_response(embed=embed)`. The view
   holds almost no state. V2 messages have **no embeds**, so this single source of
   truth disappears. State must move into an in-memory model on the view.

2. **`Edit`/`Copy` round-trip arbitrary existing posts.** They work on any one-embed
   bot message because `h.Embed` is self-describing. V2 `message.components` give you
   Containers/TextDisplays but nothing says which TextDisplay was "title" vs "footer".
   Lossless round-trip is the real hard sub-problem — easy to miss until Edit breaks
   in production.

Everything else (rendering an embed-shaped Container) is mechanical.

## Decision axes

### Axis A — How much V2 power to expose (drives everything)

| Option | What it is | Complexity | Notes |
|---|---|---|---|
| **A1 Embed-faithful** | Same fixed slots → render one Container that looks like an embed | **S–M** | Clean drop-in, UX unchanged, wastes V2. |
| **A2 Hybrid (recommended)** | A1 + two cheap superpowers: **multi-image gallery** (kills `MultiImageEmbedList`) + **accent bar** | **M** | Best value/effort; recommended core. |
| **A3 Block-based** | Ordered list of arbitrary blocks (text/gallery/section+thumb/separator/buttons), add-remove-reorder | **L–XL** | The real V2 vision, but a different editor entirely. |

### Axis B — Round-trip for Edit/Copy (forced choice)

| Option | Mechanism | Complexity | Trade-off |
|---|---|---|---|
| **B1 Component `id` as role tag (recommended)** | Assign stable int ids per role on render (title=1, body=2, image=3…); parse back on edit | **S–M** | Elegant, no external storage, lossless for posts this tool made. Needs a verify spike. |
| **B2 Persist model in DB** | Store editable model keyed by `message_id` (existing SQLAlchemy infra) | **M** | Robust, but adds a table + cleanup; only covers tracked posts. |
| **B3 Session-only** | Only edit within the same session | **S** | Feature regression — Edit/Copy on older messages stop working. |

B1 standout: makes V2 messages as round-trippable as embeds were, with no DB, using a
feature embeds never had (component IDs). hikari V2 builders accept `id=` and messages
expose `.id`.

### Axis C — Editor UI pattern (intuitiveness lever)

Current UI is a flat row of ~8 buttons — already at the clutter ceiling.

- **C1 Button-per-property** (S): familiar, doesn't scale, stays cramped.
- **C2 Single "Edit ▼" select menu (recommended)** (M): collapse the 7 edit buttons
  into one select → opens the right modal; leaves room for `Add image`, `Cancel`,
  `Done`. Scales and reads better.
- **C3 Two-level block UI** (L): only needed if A3.

## Intuitiveness improvements (independent of model choice, mostly cheap)

- **WYSIWYG preview is now real** — the preview *is* the actual Container that will be
  posted, not an approximation. Lean into it.
- **Validation toasts** — today a bad color silently logs and a bad image URL silently
  no-ops. Surface ephemeral feedback ("Couldn't parse color `xyz`").
- **Group advanced props** — tuck author/footer/thumbnail behind "Advanced ▼" so the
  common path (title, body, image, color) is uncluttered.
- **Add Cancel + Reset** — today the only exit is Done or a 14-min timeout.
- **Modal placeholders/help text** — e.g. "hex like `#EC42A5` or `pink`".

## Recommendation (staged)

1. **Core: A2 (hybrid) + B1 (id round-trip) + C2 (select-driven UI).** Makes the editor
   V2-native, kills the `MultiImageEmbedList` hack as a bonus, keeps Edit/Copy working
   with no DB, improves UX. Estimate **M, ~2–4 focused days** including posts.py
   call-site changes (return type moves from `h.Embed` to the model / a Container; the
   three commands adjust `.send`/`.edit`).
2. **Defer A3 (block-based)** unless freeform layouts are an actual product goal — a
   separate, larger project that should not gate the rest of the V2 migration.

## Risks to spike first (~half a day, before committing)

- **miru + V2 mixing**: confirm a miru `View`'s action rows can be sent *alongside* a
  hand-built preview `Container` in one `edit_response(components=[...])`. miru 4.2 has
  no V2 layout primitives, so the full component list is hand-built. Fallback if miru
  fights this: lightbulb v3 `components.Menu`.
- **Component `id` round-trip (B1)**: verify an id set on a builder survives the round
  trip and is readable on `message.components`.

## Open question (was about to ask when this was shelved)

Pick the fork before turning this into an implementation plan:
- Editor scope: **Hybrid (A2, recommended)** / Embed-faithful (A1) / Block-based (A3)
- Round-trip: **Component IDs (B1, recommended)** / Persist in DB (B2) / Session-only (B3)

## Pointers

- Editor: `dd/anchor/embeds.py`
- Consumers: `dd/anchor/extensions/posts.py`
- `MultiImageEmbedList` (obsoleted by V2 `MediaGallery`): `dd/hmessage/embed.py`
- Central dispatch to extend: `HMessage.to_message_kwargs()` in
  `dd/hmessage/message.py:119` → add sibling `to_components_v2()`.
