# Plan (FUTURE, GATED): fold "Convert to components" into "Edit components"

> **Status: APPROVED in principle, GATED (2026-07-05).** Do NOT execute until the
> anchor autopost embed→CV2 migration is done (see
> `plans/autopost_cv2_migration.md`). Rationale: while embed autoposts still exist,
> the standalone "Convert to components" command is a useful one-shot tool. Once
> everything the bot emits is already CV2, the convert path is rarely needed and the
> two-command split (gated on an invisible message flag) is pure friction — that's
> when this fold pays off. Re-verify all symbols/line numbers before executing.

## Goal

Delete the standalone **"Convert to components"** message command and give
**"Edit components"** its job: when invoked on the bot's own *non-CV2 embed*
message, auto-convert the embeds to CV2 and open the interactive editor pre-seeded
with the converted content. One right-click command that "just works" on any of the
bot's own posts, regardless of whether the target is an embed or already CV2.

Both commands live in `dd/anchor/extensions/posts.py`.

## Why this is the right move

- Today the two commands are gated on an **invisible** property (is the message
  CV2?). Users right-click, guess, and get an error half the time
  (`_reject_unless_own_cv2`, `posts.py:149`, rejects non-CV2 for Edit; Convert
  rejects already-CV2, `posts.py:334`). One command removes that guessing.
- Deletes the entire standalone Convert confirm-flow (~150 lines,
  `posts.py:319-464`) and its duplicated flag-setting edit branches.
- The interactive editor's **live preview IS the preview** the old Convert flow
  showed via Convert/Cancel buttons — arguably better, because the user can also
  *fix* the (lossy) conversion before committing instead of just accept/reject.

## The key technical insight (makes this cheap)

`Node` (in `dd/anchor/cv2_nodes.py`) is **literally Discord's raw component-payload
dict** — the same shape `fetch_raw_message_components` returns and the editor
consumes. And `embeds_to_container(...)` returns a hikari
`ContainerComponentBuilder` whose `.build()` emits exactly that dict shape.

So the "bridge" from Convert's world to Edit's world is essentially:

```python
container = embeds_to_container(message.embeds)   # dd/common/components.py:229
seed_nodes = [container.build()]                   # -> list[Node] the editor accepts
```

No new parser is needed. **Verify at build time** that `ContainerComponentBuilder.build()`
returns a plain dict (not a `(payload, attachments)` tuple) on the installed hikari
version; if it returns a tuple, take element 0.

## Implementation sketch

In `EditComponents.invoke` (`posts.py:237`):

1. Replace the hard `_reject_unless_own_cv2` gate with a branch:
   - **not the bot's own message** → error (unchanged).
   - **own + already CV2** → current path: `_load_cv2_nodes` →
     `build_components_with_user(existing_nodes=nodes)`.
   - **own + non-CV2 with embeds** → `seed_nodes = [embeds_to_container(message.embeds).build()]`,
     then `build_components_with_user(existing_nodes=seed_nodes)`.
   - **own + non-CV2 with no embeds** → "nothing to edit/convert" error.
2. On Save, edit in place with `flags=h.MessageFlag.IS_COMPONENTS_V2` (unchanged).
   For the embed path this is the irreversible conversion.
3. Delete class `ConvertToComponents` and its
   `loader.command(ConvertToComponents, ...)` registration (`posts.py:478`).
4. `embeds_to_container` import stays (now used by Edit). `build_container` may
   become unused once Convert's status messages are gone — let ruff flag it.

### Open decision (ask the user at execution time) — the irreversible flag

`IS_COMPONENTS_V2` **cannot be unset** once an edit applies it. Convert's explicit
Convert/Cancel confirm existed for exactly this. Recommended (from the advisory
pass): on the embed→CV2 path, relabel the editor's Save button to
**"Convert & Save"** and add one line of warning that saving permanently converts
the message to Components V2. `build_components_with_user` already takes
`done_button_text`; a warning-line param may need adding. Non-negotiable invariant:
**cancelling / abandoning the editor must leave the original message byte-identical**
— only the clearly-labeled save may set the flag.

## Follow-ups this unblocks / touches

- The `/help` detail page TODO for "Convert to components" (see memory
  `followup-convert-to-components-help.md`) becomes moot — instead update the
  "Edit components" help to mention it converts embed posts.
- `dd/anchor/extensions/testing.py`'s `/testing convert_sample` (manual QA aid for
  the convert path) should be re-pointed or kept as an embed source to test the
  Edit auto-convert path.

## Verification

- `embeds_to_container` already has 13 tests
  (`dd/common/tests/test_embeds_to_container.py`) — conversion logic is covered.
- Manually: right-click "Edit components" on (a) an embed post → converts, edits,
  saves as CV2; (b) an existing CV2 post → edits as before; (c) cancel on an embed
  post → original untouched, still an embed.
- `uv run ruff check`, `uv run ty`, `uv run python -m pytest`.
