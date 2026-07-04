# Copyright © 2019-present gsfernandes81

# This file is part of "dd" henceforth referred to as "destiny-director".

# destiny-director is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later version.

# "destiny-director" is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License along with
# destiny-director. If not, see <https://www.gnu.org/licenses/>.

"""Interactive Components V2 builder, built on lightbulb v3 components (miru-free).

``build_components_with_user`` shows a single ephemeral Components V2 message that is
both a **live preview** of the post and its **controls**: a block-picker select plus
Add / Edit / Delete / Move / Open / Back / Finish buttons, re-rendered on every change
and edited in place. The controls are rendered as extra action rows appended to the
preview and routed through an ``lbc.Menu`` purely by ``custom_id`` (the ``Paginator``
technique in ``dd/common/components.py``); Finish strips them, so the posted message
keeps the full component budget. Done returns the finished component tree (a list of raw
component dicts). Used by the ``/post components`` commands.

The pure node model — constructors, field specs, mutators, tree ops, validation and the
preview sanitiser — lives in ``cv2_nodes``; this module is only the interactive shell.
"""

import asyncio
import contextlib
import copy
import logging
import typing as t
import uuid

import hikari as h
import lightbulb as lb
from lightbulb import components as lbc

from . import cv2_nodes as cn
from .cv2_raw import RawComponentBuilder
from .embeds import _kyber_emoji_dict, substitute_user_side_emoji

# Fixed router custom ids. The menu matches incoming interactions on these regardless of
# where the identical-custom_id control is rendered, so the controls can live inside the
# Components V2 message alongside the preview.
_SELECT = "cv2:select"
_ADD = "cv2:add"
_EDIT = "cv2:edit"
_DELETE = "cv2:delete"
_MOVE_UP = "cv2:up"
_MOVE_DOWN = "cv2:down"
_OPEN = "cv2:open"
_BACK = "cv2:back"
_DONE = "cv2:done"
_TYPE_SELECT = "cv2:type"
_CANCEL_ADD = "cv2:cancel"

# Match the embed builder's session window; capped well under the 15-minute interaction
# token lifetime so the on-timeout disable edit still lands.
_SESSION_TIMEOUT = 840
_MODAL_TIMEOUT = 300


class _BuilderState:
    """Shared, mutable state for one Components V2 builder session."""

    def __init__(self, nodes: list[cn.Node]) -> None:
        self.nodes = nodes
        self.scope_path: list[int] = []  # container/section we're inside (empty = root)
        self.selected: int | None = None  # index of the picked child in the scope
        self.mode: str = "normal"  # "normal" or "adding"
        self.warning: list[str] = []  # validation problems shown after a blocked Finish
        self.result: list[cn.Node] | None = None  # set when the user presses Done


# --- modal ------------------------------------------------------------------------


class _NodeModal(lbc.Modal):
    """A modal that edits one node's fields, then re-renders the builder in place."""

    def __init__(
        self,
        *,
        node: cn.Node,
        emoji_dict: dict[str, h.Emoji],
        apply: t.Callable[[cn.Node], None],
        rerender: t.Callable[[], list[h.api.ComponentBuilder]],
    ) -> None:
        self._node = node
        self._emoji_dict = emoji_dict
        self._apply = apply
        self._rerender = rerender
        self._fields: list[lbc.TextInput] = []
        for label, value, required, multi_line in cn.fields_for(node):
            add = (
                self.add_paragraph_text_input
                if multi_line
                else self.add_short_text_input
            )
            self._fields.append(
                add(label, value=value or h.UNDEFINED, required=required)
            )

    async def on_submit(self, ctx: lbc.ModalContext) -> None:
        values = [ctx.value_for(field) or "" for field in self._fields]
        # Defer first so any slow work can't blow the ~3s modal ack window, then edit
        # the builder message in place.
        await ctx.interaction.create_initial_response(
            h.ResponseType.DEFERRED_MESSAGE_UPDATE
        )
        # Text content gets user-side emoji substitution (no I/O — dict pre-resolved).
        if cn.kind(self._node) == "text" and values:
            values[0] = await substitute_user_side_emoji(self._emoji_dict, values[0])
        result = cn.mutator_for(self._node)(self._node, values)
        if result is not None:
            self._apply(self._node)
        await ctx.interaction.edit_initial_response(components=self._rerender())


# --- public entry point -----------------------------------------------------------


async def build_components_with_user(
    ctx: lb.Context,
    done_button_text: str = "Post",
    existing_nodes: list[cn.Node] | None = None,
) -> list[cn.Node] | None:
    """Build a CV2 tree interactively; returns the nodes on Done, else ``None``."""
    state = _BuilderState(copy.deepcopy(existing_nodes) if existing_nodes else [])

    # Pre-resolve the emoji dict once, off the modal-ack path (a failure just disables
    # emoji substitution), exactly as the embed builder does.
    bot = t.cast(h.GatewayBot, ctx.client.app)
    try:
        emoji_dict = await _kyber_emoji_dict(bot)
    except Exception as e:
        logging.warning("CV2 builder: could not pre-resolve emoji dict: %r", e)
        emoji_dict = {}

    menu = lbc.Menu()

    def render() -> list[h.api.ComponentBuilder]:
        return _render(state, done_button_text)

    async def rerender(mctx: lbc.MenuContext) -> None:
        await mctx.respond(
            edit=True, flags=h.MessageFlag.IS_COMPONENTS_V2, components=render()
        )

    async def open_modal(
        mctx: lbc.MenuContext, node: cn.Node, apply: t.Callable[[cn.Node], None]
    ) -> None:
        modal = _NodeModal(
            node=node, emoji_dict=emoji_dict, apply=apply, rerender=render
        )
        custom_id = f"cv2_edit:{uuid.uuid4()}"
        await mctx.respond_with_modal(_modal_title(node), custom_id, components=modal)
        with contextlib.suppress(asyncio.TimeoutError):
            await modal.attach(mctx.client, custom_id, timeout=_MODAL_TIMEOUT)

    # -- callbacks -----------------------------------------------------------------

    async def on_select(mctx: lbc.MenuContext) -> None:
        state.warning = []
        values = mctx.interaction.values
        if values:
            state.selected = int(values[0])
        await rerender(mctx)

    async def on_add(mctx: lbc.MenuContext) -> None:
        state.warning = []
        state.mode = "adding"
        await rerender(mctx)

    async def on_cancel_add(mctx: lbc.MenuContext) -> None:
        state.mode = "normal"
        await rerender(mctx)

    async def on_type_select(mctx: lbc.MenuContext) -> None:
        add_kind = (mctx.interaction.values or [""])[0]
        if add_kind not in cn.ADD_LABELS:
            state.mode = "normal"
            await rerender(mctx)
            return
        node = cn.new_node_for(add_kind)
        if not cn.opens_modal_on_add(add_kind):
            _insert_at_selection(state, node)
            state.mode = "normal"
            await rerender(mctx)
            return
        state.mode = "normal"
        if cn.is_accessory_kind(add_kind):
            apply = lambda n: cn.set_accessory(state.nodes, state.scope_path, n)  # noqa: E731
        else:
            apply = lambda n: _insert_at_selection(state, n)  # noqa: E731
        await open_modal(mctx, node, apply)

    async def on_edit(mctx: lbc.MenuContext) -> None:
        node = _selected_node(state)
        if node is None or not cn.has_modal(node):
            await rerender(mctx)  # disabled button safety net
            return
        await open_modal(mctx, node, apply=lambda n: None)

    async def on_delete(mctx: lbc.MenuContext) -> None:
        state.warning = []
        if state.selected is not None:
            cn.delete_node(state.nodes, state.scope_path, state.selected)
            _clamp_selection(state)
        await rerender(mctx)

    async def on_move_up(mctx: lbc.MenuContext) -> None:
        state.warning = []
        if state.selected is not None:
            state.selected = cn.move_node(
                state.nodes, state.scope_path, state.selected, -1
            )
        await rerender(mctx)

    async def on_move_down(mctx: lbc.MenuContext) -> None:
        state.warning = []
        if state.selected is not None:
            state.selected = cn.move_node(
                state.nodes, state.scope_path, state.selected, 1
            )
        await rerender(mctx)

    async def on_open(mctx: lbc.MenuContext) -> None:
        state.warning = []
        node = _selected_node(state)
        selected = state.selected
        if node is not None and selected is not None and cn.is_container_like(node):
            state.scope_path.append(selected)
            state.selected = None
        await rerender(mctx)

    async def on_back(mctx: lbc.MenuContext) -> None:
        state.warning = []
        if state.scope_path:
            state.scope_path.pop()
            state.selected = None
        await rerender(mctx)

    async def on_done(mctx: lbc.MenuContext) -> None:
        problems = cn.validate(state.nodes)
        if problems:
            state.warning = problems
            await rerender(mctx)
            return
        state.result = copy.deepcopy(state.nodes)
        await mctx.respond(
            edit=True,
            flags=h.MessageFlag.IS_COMPONENTS_V2,
            components=_final_components(state),
        )
        mctx.stop_interacting()

    # -- register routers (custom_id -> callback) ----------------------------------

    menu.add_text_select(["_"], on_select, custom_id=_SELECT)
    menu.add_text_select(["_"], on_type_select, custom_id=_TYPE_SELECT)
    for custom_id, callback in (
        (_ADD, on_add),
        (_EDIT, on_edit),
        (_DELETE, on_delete),
        (_MOVE_UP, on_move_up),
        (_MOVE_DOWN, on_move_down),
        (_OPEN, on_open),
        (_BACK, on_back),
        (_DONE, on_done),
        (_CANCEL_ADD, on_cancel_add),
    ):
        menu.add_interactive_button(
            h.ButtonStyle.SECONDARY, callback, custom_id=custom_id, label=custom_id
        )

    await ctx.respond(
        flags=h.MessageFlag.IS_COMPONENTS_V2 | h.MessageFlag.EPHEMERAL,
        components=render(),
    )
    with contextlib.suppress(TimeoutError):
        await menu.attach(ctx.client, timeout=_SESSION_TIMEOUT)
    if state.result is None:
        with contextlib.suppress(h.NotFoundError, h.UnauthorizedError):
            await ctx.interaction.edit_initial_response(
                components=_final_components(state)
            )
    return state.result


# --- state helpers ----------------------------------------------------------------


def _selected_node(state: _BuilderState) -> cn.Node | None:
    children = cn.scope_children(state.nodes, state.scope_path)
    if state.selected is None or not (0 <= state.selected < len(children)):
        return None
    return children[state.selected]


def _clamp_selection(state: _BuilderState) -> None:
    children = cn.scope_children(state.nodes, state.scope_path)
    if not children:
        state.selected = None
    elif state.selected is not None and state.selected >= len(children):
        state.selected = len(children) - 1


def _insert_at_selection(state: _BuilderState, node: cn.Node) -> None:
    children = cn.scope_children(state.nodes, state.scope_path)
    index = state.selected + 1 if state.selected is not None else len(children)
    cn.insert_node(state.nodes, state.scope_path, index, node)
    state.selected = index


def _modal_title(node: cn.Node) -> str:
    return f"Edit {cn.ADD_LABELS.get(cn.kind(node), 'block')}"


# --- rendering --------------------------------------------------------------------


def _text(content: str) -> h.impl.TextDisplayComponentBuilder:
    return h.impl.TextDisplayComponentBuilder(content=content)


def _render(
    state: _BuilderState, done_button_text: str
) -> list[h.api.ComponentBuilder]:
    components: list[h.api.ComponentBuilder] = [
        RawComponentBuilder(node) for node in cn.sanitize_for_preview(state.nodes)
    ]
    if not state.nodes:
        components.append(_text("*No blocks yet — press **Add** to start.*"))
    if state.warning:
        components.append(
            _text(
                "### ⚠️ Not ready to post\n"
                + "\n".join(f"- {problem}" for problem in state.warning)
            )
        )
    if state.mode == "adding":
        components.extend(_adding_controls(state))
    else:
        components.extend(_normal_controls(state, done_button_text))
    return components


def _breadcrumb(state: _BuilderState) -> str:
    depth = len(state.scope_path)
    if depth == 0:
        return "-# 📄 Editing the message (top level)"
    node = cn.resolve_path(state.nodes, state.scope_path)
    where = "container" if cn.kind(node) == "container" else "section"
    return f"-# ↳ Editing inside a {where} — use **Back** to go up"


def _block_select_row(
    children: list[cn.Node], selected: int | None
) -> h.impl.MessageActionRowBuilder:
    row = h.impl.MessageActionRowBuilder()
    if not children:
        menu = row.add_text_menu(
            _SELECT, placeholder="No blocks here yet — press Add", is_disabled=True
        )
        menu.add_option("(empty)", "0")
        return row
    menu = row.add_text_menu(_SELECT, placeholder="Select a block to act on…")
    for index, child in enumerate(children):
        menu.add_option(
            cn.node_label(child)[:100], str(index), is_default=index == selected
        )
    return row


def _button(
    row: h.impl.MessageActionRowBuilder,
    custom_id: str,
    label: str,
    style: h.ButtonStyle,
    *,
    disabled: bool = False,
) -> None:
    # Build the button directly (as ``dd/common/components.py`` does): the row's
    # ``add_interactive_button`` has a stricter literal style type that rejects
    # ``ButtonStyle``.
    row.add_component(
        h.impl.InteractiveButtonBuilder(
            style=style, custom_id=custom_id, label=label, is_disabled=disabled
        )
    )


def _normal_controls(
    state: _BuilderState, done_button_text: str
) -> list[h.api.ComponentBuilder]:
    children = cn.scope_children(state.nodes, state.scope_path)
    node = _selected_node(state)
    has_sel = node is not None
    last = len(children) - 1

    row_actions = h.impl.MessageActionRowBuilder()
    _button(row_actions, _ADD, "Add", h.ButtonStyle.PRIMARY)
    _button(
        row_actions,
        _EDIT,
        "Edit",
        h.ButtonStyle.SECONDARY,
        disabled=not (has_sel and cn.has_modal(node)),
    )
    _button(row_actions, _DELETE, "Delete", h.ButtonStyle.DANGER, disabled=not has_sel)
    _button(
        row_actions,
        _MOVE_UP,
        "Move up",
        h.ButtonStyle.SECONDARY,
        disabled=not has_sel or state.selected == 0,
    )
    _button(
        row_actions,
        _MOVE_DOWN,
        "Move down",
        h.ButtonStyle.SECONDARY,
        disabled=not has_sel or state.selected == last,
    )

    row_nav = h.impl.MessageActionRowBuilder()
    _button(
        row_nav,
        _OPEN,
        "Open ▸",
        h.ButtonStyle.SECONDARY,
        disabled=not (has_sel and cn.is_container_like(node)),
    )
    _button(
        row_nav,
        _BACK,
        "◂ Back",
        h.ButtonStyle.SECONDARY,
        disabled=not state.scope_path,
    )
    _button(row_nav, _DONE, done_button_text, h.ButtonStyle.SUCCESS)

    return [
        _text(_breadcrumb(state)),
        _block_select_row(children, state.selected),
        row_actions,
        row_nav,
    ]


def _adding_controls(state: _BuilderState) -> list[h.api.ComponentBuilder]:
    row = h.impl.MessageActionRowBuilder()
    menu = row.add_text_menu(_TYPE_SELECT, placeholder="Choose a block type to add…")
    for add_kind in cn.addable_kinds(state.nodes, state.scope_path):
        menu.add_option(cn.ADD_LABELS[add_kind], add_kind)
    cancel_row = h.impl.MessageActionRowBuilder()
    _button(cancel_row, _CANCEL_ADD, "Cancel", h.ButtonStyle.SECONDARY)
    return [_text("**Add a block** — pick a type:"), row, cancel_row]


def _final_components(state: _BuilderState) -> list[h.api.ComponentBuilder]:
    """The controls-free preview shown once the session ends (Done or timeout)."""
    components: list[h.api.ComponentBuilder] = [
        RawComponentBuilder(node) for node in cn.sanitize_for_preview(state.nodes)
    ]
    if not components:
        components.append(_text("*(empty)*"))
    return components
