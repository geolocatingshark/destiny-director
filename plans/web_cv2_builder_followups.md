# Web CV2 builder — follow-ups

## Status: the builder is BUILT and on `dev`; these are the known remainders

Everything implemented is on `origin/dev` (`74c920f` … `2926827`). The decisions behind it
are in **`plans/web_cv2_builder.md`** — read that first; this file is only what is *left*.

Nothing here is blocking. The builder is in daily-usable shape: authoring, editing and
copying CV2 posts all work on desktop and mobile, and the in-Discord builder it replaced
has been deleted.

---

## 1. Custom emoji on buttons don't work

**The Emoji field on a link button invites input that silently does nothing.**

`mutate_link_button` (and the web inspector's `btnEmoji` handler) store
`{"name": "<whatever was typed>"}`. That shape is only valid for a **unicode** emoji.
A custom guild emoji needs `{"id": "849727805994565662", "name": "LS"}` — without the id,
Discord renders no emoji at all.

**Fix sketch.** The page already loads a guild emoji map for the preview
(`cv2_builder_page._emoji_map`, `{name: url}`), and the id is embedded in the CDN URL
(`…/emojis/<id>.png`). Either parse it out, or — cleaner — widen the payload to
`{name: {url, id, animated}}` and have the inspector resolve a typed `:name:` (or a bare
name) to `{id, name}`, falling back to `{name}` for a literal unicode character.
Server-side `mutate_link_button` is currently dead code (see §3) so only the web path
needs changing, but keep the two in step if that layer is revived.

**Test it with:** a button whose emoji is a real guild emoji, sent for real — the preview
already resolves custom emoji correctly, so a green preview proves nothing here.

---

## 2. Media gallery items have no alt text or spoiler

Discord supports `description` (alt text) and `spoiler` per gallery item; we only author
`items[i].media.url`. Existing values **survive** an edit (the inspector reassigns
`.media` only, leaving siblings intact) — they are simply not editable, and the preview
ignores them.

Alt text is an accessibility gap, not just a missing feature. The inspector's per-image
rows are the obvious home for both fields; the pattern to copy is the per-button field
groups added for multi-button rows (`.cv2b-btn-list`).

---

## 3. Dead code left by deleting the in-Discord builder

`cv2_nodes.py` was written to serve `cv2_builder.py`'s modal-driven UI. That UI is gone
(`51b4e0a`), and the web builder mirrors the model in JS (`web_static/cv2_model.js`) and
does tree operations client-side. What the server still genuinely uses is small:

> `Node`, the type constants, `kind`, `_button_of` / `_buttons_of`,
> `sanitize_for_preview`, `validate`

Orphaned (verified by grep — no non-test caller outside `cv2_nodes` itself):

- **Field-spec / mutator layer:** `text_fields`, `container_fields`, `separator_fields`,
  `media_fields`, `link_button_fields`, `thumbnail_fields`, every `mutate_*`, `_FIELDS`,
  `_MUTATORS`, `fields_for`, `mutator_for`, `has_modal`, `_parse_bool`
- **Add-flow catalogue:** `ADD_LABELS`, `_IMMEDIATE_ADD`, `_ADD_CONSTRUCTORS`,
  `new_node_for`, `opens_modal_on_add`, `is_accessory_kind`, `addable_kinds`
- **Tree-op state machine:** `children_ref`, `resolve_path`, `scope_children`,
  `scope_is_section`, `insert_node`, `delete_node`, `move_node`, `set_accessory`
- **Labels:** `node_label`, `_preview`, `is_container_like`
- Most `make_*` constructors (the web builder constructs nodes client-side)

Roughly 250 lines. Deliberately **not** removed alongside the correctness fixes so the
two are reviewable apart. Delete the tests that only cover the dead layer with it, and
keep `addable_kinds`' nesting rules documented somewhere — `cv2_model.js` is the live
copy of those rules now.

---

## 4. Deliberately out of scope (record, don't re-litigate)

- **Non-link buttons** (styles 1–4) and **select menus** (types 3, 5–8) need a live
  interaction handler; a posted announcement has none.
- **File components** (type 13) need a real uploaded attachment, not a URL. They
  round-trip when editing an existing post and cannot be authored — the pre-existing
  behaviour, unchanged.
- **Premium/SKU buttons** (style 6) — same reason as non-link buttons.
- **Free-form editing of weekly-reset / Trials posts** — owner call; see
  `plans/web_cv2_builder.md` for the reasoning (they regenerate from `hybrid_post_core`).

---

## 5. Not on prod

The builder has only ever run on the **dev** Railway environment. Promoting it needs the
additive migration **`migrations/20260803030737.sql`** (`cv2_draft`), which the container
entrypoint applies automatically via `atlas migrate apply`. Already exercised against a
real MySQL 8 for the JSON round-trip, creator scoping and the prune `DATETIME` compare.

---

## 6. Interactions no automated test covers

There is no browser on the dev box, so every DOM behaviour below was verified by hand on
a phone, not by a test. A browser-driving harness (Playwright) would be the single
highest-value addition to this area:

- pointer-event drag: reorder, re-parent, the accessory slot, edge autoscroll
- long-press → context menu, and its swallowing of the synthesized click
- the contenteditable editor: caret position after accepting an emoji suggestion,
  backspacing across an emoji atom, IME/autocorrect on Android
- the properties sheet: dismissal, scroll clearance, re-opening
- the mobile media queries (a cascade-order mistake once silently killed **all** of them,
  and only a screenshot caught it — see the warning comment at the end of
  `cv2_builder.css`)

`web_static/tests/cv2_model.test.js` covers the pure model well (65 tests); it is
specifically the DOM layer that is untested.
