# Plan: Completely remove `miru` from destiny-director

> **Status: DRAFT — NEEDS RE-REVIEW. Do NOT execute without user review.**
> Authored during planning on the `feature-lightbulb-v3` branch. Re-read against the
> current tree before implementing (file/line numbers may have drifted).
>
> **Update (2026-07-01):** being approached incrementally rather than as the single
> sweep below — another agent is adding edit abilities to Components V2 posts (see
> `plans/components_v2_embed_editor.md`), which de-mirus the `dd/anchor/embeds.py`
> editor as a side effect. The nav rewrite + `uv remove hikari-miru` are still
> outstanding, so the `<4` pin is not gone yet.

## Context

The bot is mid-migration to **hikari-lightbulb v3** (branch `feature-lightbulb-v3`).
`hikari-miru` (pinned `>=3.4.0,<4`) is the last library blocking a clean v3 stack —
an intentional holdback. Lightbulb v3 now ships native equivalents for everything we
still use miru for (`lightbulb.components.Menu` for buttons, `lightbulb.components.Modal`
for modals), and the codebase already proves the pattern: `dd/common/components.py`
(`Paginator`) and `dd/beacon/extensions/mirror.py` are fully miru-free, built on
`lbc.Menu` + a custom-id→callback router. Removing miru drops the `<4` pin, collapses
the component stack onto one library, and unblocks deferred dependency bumps.

Miru is used in **exactly 4 files**; the rest is downstream call-site touch-ups, the
`uv` removal, and tests.

| File | Miru usage | Replacement |
|------|-----------|-------------|
| `dd/beacon/__main__.py` | `import miru` + `miru.install(bot)` | delete (lightbulb client already started) |
| `dd/anchor/__main__.py` | `import miru as m` + `m.install(bot)` | delete |
| `dd/beacon/nav.py` | `miru.ext.nav` navigator (View + 3 buttons) | rewrite on `lbc.Menu` |
| `dd/anchor/embeds.py` | `m.View` + 8 `@m.button` + `m.Modal`/`m.TextInput` | rewrite on `lbc.Menu` + `lbc.Modal` |

**Linchpin (verified):** `miru.install` has no replacement — lightbulb's `client.start()`
(already called in both entry points) wires component/modal handling via
`client._attached_menus` / `_attached_modals`. `lbc.Menu`/`lbc.Modal` are hand-written
`__slots__` classes (not attrs), so no new `ty.toml` overrides are expected for them.

---

## Confirmed lightbulb v3 APIs (docs + the 3.2.3 source in the sibling venv)

- **Menu** (`from lightbulb import components as lbc`):
  `menu.add_interactive_button(style, on_press, *, custom_id=, label=, emoji=, disabled=)`;
  `await menu.attach(client, *, timeout=)` blocks until `ctx.stop_interacting()` or
  `TimeoutError`; `MenuContext.respond(content, *, edit=False, ephemeral=False, ...)`
  (`edit=True` edits the initial response); `menu.disable_all_components()`.
- **Modal**: subclass `lbc.Modal`, implement `async def on_submit(self, ctx: ModalContext) -> R`;
  add inputs in `__init__` via `self.f = self.add_short_text_input(label, *, value=, required=, ...)`
  / `add_paragraph_text_input(...)`; read with `ctx.value_for(self.f) -> str | None`.
  Present from a button press: `await mctx.respond_with_modal(title, custom_id, components=modal)`
  (must be the **first** response on that `MenuContext`); then
  `await modal.attach(client, custom_id, timeout=)` returns `on_submit`'s value, or
  raises `asyncio.TimeoutError` on dismiss. **`custom_id` must be unique per open**
  (single `client._attached_modals` dict) — generate `f"embed_edit:{uuid.uuid4()}"`
  and pass the same id to both calls.
- `mctx.client` / a modal's `ctx.client` give the lightbulb `Client` inside callbacks.

---

## 1. `dd/beacon/nav.py` — rewrite the navigator on `lbc.Menu`

The miru-coupled pieces are only: `NavigatorView(nav.NavigatorView)`, `IndicatorButton`,
`NextButton`, `PrevButton`, and the `m.Context`/`m.ViewContext` hints. **Everything else
stays byte-for-byte**: `DateRangeDict`, `NavPages`, `ResetPages`, `NavPagesHolder`,
`setup_nav_pages`, `NO_DATA_HERE_EMBED`, the emoji constants, and the `_history_updater`/
lookahead machinery. The navigator's model differs from `Paginator` (relative date-indexed
pages, lazy lookup, date label) so it stays a **standalone class**, not a `Paginator` subclass.

Follow the `Paginator` send/attach/edit/timeout shape (`dd/common/components.py:289-360`).

1. Remove `import miru as m` and `from miru.ext import nav` (lines 26, 28). Keep
   `from typing import override`, the `asyncio`/`random` imports (used by `NavPages`).
   Add `from lightbulb import components as lbc`. Add module constants
   `_NAV_PREV_CUSTOM_ID = "dd_nav:prev"`, `_NAV_NEXT_CUSTOM_ID = "dd_nav:next"`,
   `_NAV_INDICATOR_CUSTOM_ID = "dd_nav:indicator"`.
2. **Add a pure helper** (testable, mirrors `components.py:nav_buttons_row`):
   ```python
   def build_nav_row(*, current_page, history_len, lookahead_len,
                     date_label, all_disabled=False) -> list[h.api.InteractiveButtonBuilder]
   ```
   - prev disabled when `all_disabled or current_page <= 1 - history_len`
     (from old `PrevButton.before_page_change`, line 728)
   - next disabled when `all_disabled or current_page >= lookahead_len`
     (from old `NextButton.before_page_change`, line 699)
   - indicator: `SECONDARY`, `is_disabled=True`, `label=date_label`,
     custom_id = `_NAV_INDICATOR_CUSTOM_ID`. Use `h.impl.InteractiveButtonBuilder` + the emoji constants.
3. **Rewrite `class NavigatorView`** as a plain class:
   - `__init__(self, *, pages: NavPages, timeout=navigator_timeout, allow_start_on_blank_page=False, display_date_offset=dt.timedelta(0))` — **drop `autodefer`**.
     Port the start-page computation verbatim (lines 212-221). Build `self._menu = lbc.Menu()`
     and register prev/next via `add_interactive_button(..., custom_id=_NAV_PREV/_NEXT, emoji=...)`.
     Keep the `current_page` property + clamping setter (lines 320-336). Track `_ctx`, `_message`.
   - `_date_label(self) -> str`: port the old `IndicatorButton.before_page_change` (lines 666-670):
     `date = pages.index_to_date(current_page) + display_date_offset`, then
     `f"{date.strftime('%B %-d')}{get_ordinal_suffix(date.day)}"`. (`%-d` is a glibc
     extension already in use; deployment is Linux — fine.)
   - `_render_payload(self, *, all_disabled=False) -> dict`: `page = self._pages[self._current_page]`;
     `payload = page.to_message_kwargs()`; **preserve the attachment-clearing trick**
     (old `send_page`, lines 289-297 — when no attachments, pass `attachment=None`, not
     `attachments=[]`, or stale attachments persist on edit); set
     `payload["components"] = [build_nav_row(...)]` when paginated, else `[]`.
   - `needs_pagination` property: `history_len + lookahead_len > 1` (old `get_default_buttons`, line 303).
   - `async def send(self, ctx: lb.Context)`:
     - single page → `await ctx.respond(**self._render_payload())`; return (no attach).
     - else `await ctx.respond(**self._render_payload())`;
       `self._ctx = ctx`; `self._message = await ctx.interaction.fetch_initial_response()`;
       `await self._menu.attach(ctx.client, timeout=self._timeout)`; then `await self._on_timeout()`.
   - `_on_prev`/`_on_next(self, mctx)`: adjust `self.current_page`, then
     `await mctx.respond(edit=True, **self._render_payload())`. **No pre-`defer`.**
   - `_on_timeout(self)`: components-only disable (mirror `Paginator._on_timeout`,
     `components.py:346-360`): `await self._ctx.interaction.edit_message(self._message, components=[build_nav_row(..., all_disabled=True)])`.
4. **Delete** `IndicatorButton`, `NextButton`, `PrevButton` (lines 642-728) and the miru
   `_get_page_payload` / `send_page` / `get_default_buttons` overrides (folded into the above).
5. **`make_navigator_command`** (lines 801-829): drop the `autodefer` param + its forward;
   change `navigator.send(ctx.interaction)` → `navigator.send(ctx)`. Keep `name`,
   `description`, `allow_start_on_blank_page`, `display_date_offset`.

### Downstream nav consumers
- The 8 helper-based extensions (gunsmith, weekly_reset, trials, twab, nightfall,
  eververse, xur, lost_sector) go through `make_navigator_command` and pass **no**
  `autodefer` → **zero changes**.
- `dd/beacon/extensions/ada.py:53` (and the dead `if not SINGLE_PAGE_MODE` branch):
  `.send(ctx.interaction)` → `.send(ctx)`. Keep `timeout=60`.
- `dd/beacon/extensions/template.py:87`: `.send(ctx.interaction)` → `.send(ctx)`
  (dead under `IGNORE=True`, but keep it valid so it type-checks).

---

## 2. `dd/anchor/embeds.py` — rewrite the embed builder on `lbc.Menu` + `lbc.Modal`

Public surface to preserve (3 callers in `dd/anchor/extensions/posts.py:35,87,107`):
`async def build_embed_with_user(ctx, done_button_text="Done", existing_embed=None) -> h.Embed | None`.
`substitute_user_side_emoji` (lines 31-47) has **no miru usage — keep it unchanged**.
Delete `InteractiveBuilderView`, `EmbedBuilderView`, and the old `ask_user_for_properties`
helper (private, no external importers).

> Cross-ref: `plans/components_v2_embed_editor.md` is a separate deferred idea for a CV2
> rebuild of this editor — out of scope here; this plan only de-mirus the existing flow.

1. Remove `import miru as m` (line 21). Add `import asyncio`, `import uuid`,
   `from lightbulb import components as lbc`.
2. **`class _PropertiesModal(lbc.Modal)`** — dynamic 1..N inputs:
   ```python
   def __init__(self, names, values, required, multi_line, mutate, embed):
       self._fields = [(add)(n, value=v or h.UNDEFINED, required=r)
                       for n, v, r in zip(names, values, required, strict=True)]
       # add = self.add_paragraph_text_input if multi_line else self.add_short_text_input
   async def on_submit(self, ctx):
       values = [ctx.value_for(f) or "" for f in self._fields]
       new = await self._mutate(self._embed, values)   # field-specific mutation
       if new is not None:
           await ctx.interaction.create_initial_response(h.ResponseType.MESSAGE_UPDATE, embed=new)
       else:
           await ctx.interaction.create_initial_response(h.ResponseType.MESSAGE_UPDATE)
   ```
   The mutation runs **inside `on_submit`** so the message edit + ack happen atomically
   within Discord's **3-second modal-ack window** (see constraints below).
3. **`build_embed_with_user`**:
   - Build the initial embed (same as lines 297-301).
   - **Pre-resolve the emoji dict once** (e.g. read `bot.emoji` off the
     `ServerEmojiEnabledBot`, or fetch up front) and close over it, so the description
     mutator calls `substitute_user_side_emoji(emoji_dict, ...)` (dict branch, **no network**)
     and stays inside the ack window. (eververse/gunsmith already call it with a dict.)
   - `menu = lbc.Menu()`; register the 8 edit buttons + Done via `add_interactive_button`.
     For embeds, render the menu's **own** rows directly (`components=menu`) — no CV2
     constraint here; 9 buttons auto-wrap to 2 action rows (5 + 4), within limits.
   - `await ctx.respond(embed=embed, components=menu, flags=h.MessageFlag.EPHEMERAL)`.
   - `await menu.attach(ctx.client, timeout=840)`.
   - After attach: if no Done was pressed (`holder.result is None`), disable via
     `await ctx.interaction.edit_initial_response(components=menu.disable_all_components())`.
     Return `holder.result`. Use a small non-slotted **holder object** (like `NavPagesHolder`)
     to carry the final embed out of the Done callback — do **not** subclass `Menu` (slots).
4. **Each edit-button callback** `(mctx)`:
   - `if not mctx.interaction.message.embeds: return` (guard); `embed = mctx.interaction.message.embeds[0]`.
   - `cid = f"embed_edit:{uuid.uuid4()}"`; build `_PropertiesModal(..., mutate=<field mutator>, embed=embed)`.
   - `await mctx.respond_with_modal(title, cid, components=modal)` — **first** action on `mctx`
     (the modal *is* the button's ack; do not `respond`/`defer` before it).
   - `try: await modal.attach(mctx.client, cid, timeout=...)` `except asyncio.TimeoutError: return`
     (dismiss → leave field untouched).
   - The mutator (a small async fn per field) returns the new embed (or `None` to no-op):
     - **Preserve None-vs-empty semantics** the old code had: dismiss = `TimeoutError` → no change;
       empty string returned = clear the property (image/thumbnail/author/footer; lines 220-225,
       243-250, 198-204, 270-276).
     - description mutator: `substitute_user_side_emoji(emoji_dict, value)`; image/thumbnail:
       `follow_link_single_step(url)` (single redirect, typically <1s — within budget).
   - **Done callback**: `holder.result = mctx.interaction.message.embeds[0]`;
     `await mctx.respond(edit=True, components=menu.disable_all_components())`; `mctx.stop_interacting()`.
5. `dd/anchor/extensions/posts.py` — **no changes** (signature preserved; the ephemeral
   builder remains the command's initial response, so the follow-up `ctx.respond("Posting…")`
   still routes correctly).

### Hard constraints (verified against lightbulb 3.2.3 source)
- `respond_with_modal` raises if an initial response was already sent on that context →
  it must be the first response in the button callback.
- modal `custom_id` must be unique per concurrent open (uuid suffix) — avoids the
  `_attached_modals` clobber that `mirror.py` namespaces against.
- modal submit must edit-and-ack within ~3s → keep network calls off that path (emoji
  pre-resolved; image follow is a fast single redirect).

---

## 3. Entry points — drop `miru.install`

- `dd/beacon/__main__.py`: delete `import miru` (line 24) and `miru.install(bot)` (line 88);
  fix the module docstring (line 19) "miru and lightbulb" → "lightbulb".
- `dd/anchor/__main__.py`: delete `import miru as m` (line 24) and `m.install(bot)` (line 104);
  fix the docstring (line 18). The `m` alias is only used for `m.install` — clean removal.
- Both already `await client.start()` inside `on_starting_event`, which is all the
  component/modal machinery needs.

---

## 4. Dependency removal

Run with the **Bash sandbox disabled** (uv cache `~/.cache/uv` is read-only under the sandbox):

```
uv remove hikari-miru
uv sync
```

`hikari-miru` is a **leaf** dep (pyproject.toml line ~20; nothing else in `uv.lock`
depends on it; transitive `colorama` is win-only, `hikari` is a direct dep), so nothing
is orphaned. Confirm `uv.lock` no longer contains a `hikari-miru` package entry.

---

## 5. Tests (pure-render, no Discord I/O)

Mirror `dd/beacon/tests/test_render_mirror_progress.py` (asserts on returned
`list[h.api.ComponentBuilder]`).

- **`dd/beacon/tests/test_render_nav.py`** — test `build_nav_row`:
  - prev disabled exactly at `current_page == 1 - history_len`, enabled above it;
  - next disabled exactly at `current_page == lookahead_len`, enabled below it;
  - indicator `is_disabled=True` and its `label` equals the expected formatted date
    (known `current_page` + `display_date_offset`, incl. an ordinal-suffix case like 1st/2nd/3rd/21st).
- **`dd/anchor/tests/test_embeds.py`** — construct a `_PropertiesModal` with mixed
  single/paragraph fields and assert the field count/labels/`required` flags and short-vs-
  paragraph style; assert the default-embed builder produces the expected title/description/color.
  (Create `dd/anchor/tests/__init__.py` if the dir is new.)

---

## 6. Verification (automated only)

Run with the Bash sandbox disabled:

1. `rg -n "miru" dd/` → only the descriptive docstring in `dd/common/components.py:20`
   may remain (optionally reword its "eventually replace `dd.beacon.nav`" line).
   No `import miru`, no `miru.`/`m.install`, no `nav.NavButton` anywhere.
2. `uv run ruff check` and `uv run ruff format` — clean. Watch B008 (keep button
   styles/emoji as module constants, never in arg defaults) and F401 (no leftover imports).
3. `uv run ty check` — clean. Expect no new overrides; if `mctx.respond(**payload)` or
   `embeds[0]` indexing trips ty, prefer a guard over an inline ignore (only fall back to
   `# ty: ignore[code]` with a comment, per CLAUDE.md).
4. `uv run python -m pytest` — full suite green, including the two new test modules.

---

## 7. Follow-ups (post-merge, outside this change)

- Update agent memory `dependency-updates-deferred`: miru is no longer a holdback; the
  `<4` pin is gone.

---

## Critical files

- `dd/beacon/nav.py` — navigator rewrite (keep all data classes)
- `dd/anchor/embeds.py` — embed-builder rewrite
- `dd/beacon/__main__.py`, `dd/anchor/__main__.py` — drop `miru.install`
- `dd/beacon/extensions/ada.py`, `dd/beacon/extensions/template.py` — `.send(ctx)` one-liners
- `pyproject.toml` + `uv.lock` — via `uv remove hikari-miru`
- **Templates to follow:** `dd/common/components.py` (`Paginator`),
  `dd/beacon/extensions/mirror.py` (`lbc.Menu` + namespaced custom-ids + pure render fn),
  `dd/beacon/tests/test_render_mirror_progress.py` (test style)
