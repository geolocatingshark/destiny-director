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

"""Bot-agnostic, miru-free interactive components.

This module provides a :class:`Paginator` built directly on lightbulb v3's
``lightbulb.components.Menu`` plus Discord Components V2 (CV2) builders, with no
dependency on miru. It is kept general enough to eventually replace
``dd.beacon.nav`` but currently focuses on the needs of the ``/help`` command.

CV2 composition (confirmed via spike):
    - A page is a factory producing a list of hikari component builders, sent with
      the ``hikari.MessageFlag.IS_COMPONENTS_V2`` flag. The help layout uses a single
      :class:`hikari.impl.ContainerComponentBuilder` per page (text displays +
      separators) with the navigation buttons rendered inside it via an action row.
    - ``lightbulb.components.Menu`` is used purely as the ``custom_id`` -> callback
      router: its interaction handler matches on ``interaction.custom_id`` against
      the buttons added to the menu, independent of *where* those buttons are
      rendered. We therefore add the nav buttons to the menu (to register the
      handlers) and render visually-identical buttons (same custom ids) inside the
      CV2 container.
    - On every press/timeout we rebuild the whole container from scratch with the
      buttons reflecting the current page index / disabled state, then edit the
      message in place.

A classic-embed fallback mode is also supported: pass ``h.Embed`` pages and the
paginator sends/edits them as embeds driven by the exact same menu buttons.
"""

import typing as t

import hikari as h
import lightbulb as lb
from lightbulb import components as lbc

from . import cfg

# Unicode reverse (◀) / play (▶) triangles used as the prev/next button emoji.
# Defined as module-level constants so they are not constructed in function-argument
# defaults (ruff B008).
PREV_PAGE_EMOJI = chr(9664)
NEXT_PAGE_EMOJI = chr(9654)

_PREV_CUSTOM_ID = "dd_paginator:prev"
_INDICATOR_CUSTOM_ID = "dd_paginator:indicator"
_NEXT_CUSTOM_ID = "dd_paginator:next"

# A CV2 page is a *factory* that builds a fresh ordered list of component builders
# each time it is rendered. A factory (rather than a prebuilt list) is used so the
# nav buttons can be (re)injected into a freshly-built container on every press
# without mutating shared builder objects across renders. A fallback page is a
# single embed.
Cv2PageFactory = t.Callable[[], list[h.api.ComponentBuilder]]
Page = Cv2PageFactory | h.Embed


# ---------------------------------------------------------------------------
# CV2 layout helpers
# ---------------------------------------------------------------------------


def text_display(content: str) -> h.impl.TextDisplayComponentBuilder:
    """Build a CV2 text display component."""
    return h.impl.TextDisplayComponentBuilder(content=content)


def separator(*, divider: bool = True) -> h.impl.SeparatorComponentBuilder:
    """Build a CV2 separator component."""
    return h.impl.SeparatorComponentBuilder(divider=divider)


def nav_buttons_row(
    *,
    page_index: int,
    page_count: int,
    all_disabled: bool = False,
) -> list[h.api.InteractiveButtonBuilder]:
    """Build the prev / indicator / next interactive buttons for an action row.

    The custom ids match the ones the :class:`Paginator` registers on its menu, so
    presses on these (rendered inside a CV2 container) route to the menu callbacks.

    Args:
        page_index: Zero-based index of the currently displayed page.
        page_count: Total number of pages.
        all_disabled: If ``True`` every button is disabled (used on timeout).
    """
    prev_disabled = all_disabled or page_index <= 0
    next_disabled = all_disabled or page_index >= page_count - 1
    return [
        h.impl.InteractiveButtonBuilder(
            style=h.ButtonStyle.SECONDARY,
            custom_id=_PREV_CUSTOM_ID,
            emoji=PREV_PAGE_EMOJI,
            is_disabled=prev_disabled,
        ),
        h.impl.InteractiveButtonBuilder(
            style=h.ButtonStyle.SECONDARY,
            custom_id=_INDICATOR_CUSTOM_ID,
            label=f"{page_index + 1}/{page_count}",
            is_disabled=True,
        ),
        h.impl.InteractiveButtonBuilder(
            style=h.ButtonStyle.SECONDARY,
            custom_id=_NEXT_CUSTOM_ID,
            emoji=NEXT_PAGE_EMOJI,
            is_disabled=next_disabled,
        ),
    ]


def build_container(
    text_sections: t.Sequence[str],
    *,
    accent_color: h.Color | None = None,
) -> h.impl.ContainerComponentBuilder:
    """Build a CV2 container with one text display per section, separated by dividers.

    Args:
        text_sections: Markdown blocks; each becomes its own text display with a
            divider separator in between.
        accent_color: The container accent colour (defaults to
            ``cfg.embed_default_color``).
    """
    container = h.impl.ContainerComponentBuilder(
        accent_color=accent_color
        if accent_color is not None
        else cfg.embed_default_color
    )
    for i, section in enumerate(text_sections):
        if i:
            container.add_separator(divider=True)
        container.add_text_display(section)
    return container


def rebuild_components(
    components: t.Sequence[h.PartialComponent],
) -> list[h.api.ComponentBuilder]:
    """Rebuild sendable CV2 component *builders* from fetched component *models*.

    hikari's ``create_message``/``edit`` accept only builders, but a fetched message
    exposes component *models* which are not builders and carry no build path (unlike
    embeds, which round-trip directly). The mirror uses this to re-send a Components
    V2 source message to a destination. Only the CV2 content types we actually emit
    are supported — containers, text displays and separators; an unsupported type
    raises so a new component kind surfaces loudly instead of mirroring blank.
    """
    return [_rebuild_component(component) for component in components]


def _rebuild_component(component: h.PartialComponent) -> h.api.ComponentBuilder:
    if isinstance(component, h.ContainerComponent):
        container = h.impl.ContainerComponentBuilder(
            accent_color=component.accent_color
            if component.accent_color is not None
            else h.UNDEFINED,
            spoiler=component.is_spoiler,
        )
        # Add children via the typed methods (the generic ``add_component`` only
        # accepts the narrow container-child union). Containers can't nest in CV2.
        for child in component.components:
            if isinstance(child, h.TextDisplayComponent):
                container.add_text_display(child.content)
            elif isinstance(child, h.SeparatorComponent):
                container.add_separator(
                    spacing=child.spacing if child.spacing is not None else h.UNDEFINED,
                    divider=child.divider if child.divider is not None else h.UNDEFINED,
                )
            else:
                raise NotImplementedError(_unrebuildable(child))
        return container
    if isinstance(component, h.TextDisplayComponent):
        return h.impl.TextDisplayComponentBuilder(content=component.content)
    if isinstance(component, h.SeparatorComponent):
        return h.impl.SeparatorComponentBuilder(
            spacing=component.spacing if component.spacing is not None else h.UNDEFINED,
            divider=component.divider if component.divider is not None else h.UNDEFINED,
        )
    raise NotImplementedError(_unrebuildable(component))


def _unrebuildable(component: h.PartialComponent) -> str:
    kind = getattr(component, "type", component)
    return f"Cannot rebuild CV2 component of type {kind!r}"


# Char/line limits ported from the lightbulb v2 ``lines_to_embeds`` paginator
# (``git show 3a92d4f:dd/beacon/modules/help.py``).
MAX_PAGE_CHARS = 1800
MAX_PAGE_LINES = 64
MAX_LINE_CHARS = 2000


def chunk_lines_to_sections(lines: t.Sequence[str]) -> list[str]:
    """Chunk help lines into page-sized markdown sections.

    Ports the ~1800-char / ~64-line page limits from the v2 ``lines_to_embeds``.
    Over-long individual lines are truncated to 2000 chars. Each returned string is
    one page's worth of content (newline-joined lines).
    """
    pages: list[str] = [""]
    for raw_line in lines:
        line = raw_line
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS]

        current = pages[-1]
        if current and (
            len(current) + len(line) > MAX_PAGE_CHARS
            or len(current.split("\n")) >= MAX_PAGE_LINES
        ):
            pages.append("")
            current = ""

        pages[-1] = (current + "\n" + line) if current else line

    return [p for p in pages if p] or [""]


# ---------------------------------------------------------------------------
# Paginator
# ---------------------------------------------------------------------------


class Paginator:
    """A miru-free paginator built on ``lightbulb.components.Menu``.

    Holds an ordered list of pages. In CV2 mode each page is a *factory* that builds
    a fresh list of component builders on demand (so the nav buttons can be injected
    into a freshly-built container each render); in fallback mode pages are
    ``h.Embed`` instances. Renders prev / disabled page-indicator / next buttons,
    disabling prev on the first page and next on the last. Edits the message in place
    and, on timeout, disables the controls.

    The page factories should *not* include the nav buttons; the paginator injects a
    fresh nav row reflecting the current index (inside the page's last container when
    present, otherwise as a top-level action row). Pass ``h.Embed`` pages for the
    classic-embed fallback.
    """

    def __init__(
        self,
        pages: t.Sequence[Page],
        *,
        timeout: int | None = None,
    ) -> None:
        if not pages:
            raise ValueError("Paginator requires at least one page")
        self._pages: list[Page] = list(pages)
        self._cv2 = not isinstance(self._pages[0], h.Embed)
        self._timeout = timeout if timeout is not None else int(cfg.navigator_timeout)
        self._index = 0
        # Captured in ``send`` so the controls can be disabled on timeout.
        self._ctx: lb.Context | None = None
        self._message: h.Message | None = None

        # The menu is used purely to route button presses to callbacks; its handler
        # matches on ``interaction.custom_id`` regardless of where the button is
        # rendered, so for CV2 we render visually-identical buttons (same custom ids)
        # inside the container and never send the menu's own rows.
        self._menu = lbc.Menu()
        self._menu.add_interactive_button(
            h.ButtonStyle.SECONDARY,
            self._on_prev,
            custom_id=_PREV_CUSTOM_ID,
            emoji=PREV_PAGE_EMOJI,
        )
        self._menu.add_interactive_button(
            h.ButtonStyle.SECONDARY,
            self._on_next,
            custom_id=_NEXT_CUSTOM_ID,
            emoji=NEXT_PAGE_EMOJI,
        )

    @property
    def page_count(self) -> int:
        return len(self._pages)

    @property
    def needs_pagination(self) -> bool:
        """Whether more than one page exists (and controls are worthwhile)."""
        return len(self._pages) > 1

    # -- rendering ---------------------------------------------------------

    def _render_components(
        self, *, all_disabled: bool = False
    ) -> list[h.api.ComponentBuilder]:
        """Build the component list for the current CV2 page.

        The nav buttons are rendered inside the last container (so they share its
        accent styling) when the page ends with a container; otherwise they are
        appended as a top-level action row. The page factory is re-invoked on every
        render so no builder object is mutated across presses.
        """
        page = self._pages[self._index]
        if isinstance(page, h.Embed):
            raise TypeError("CV2 page must be a component factory, not an Embed")
        components: list[h.api.ComponentBuilder] = list(page())

        # No nav controls for a single page.
        if not self.needs_pagination:
            return components

        nav = nav_buttons_row(
            page_index=self._index,
            page_count=self.page_count,
            all_disabled=all_disabled,
        )
        if components and isinstance(components[-1], h.impl.ContainerComponentBuilder):
            components[-1].add_action_row(nav)
        else:
            row = h.impl.MessageActionRowBuilder()
            for button in nav:
                row.add_component(button)
            components.append(row)
        return components

    def _current_embed(self) -> h.Embed:
        page = self._pages[self._index]
        if not isinstance(page, h.Embed):
            raise TypeError("Expected an Embed page")
        return page

    # -- sending / editing -------------------------------------------------

    async def send(self, ctx: lb.Context) -> None:
        """Send page 1 from a ``lb.Context`` and attach the paginator.

        If only a single page exists no controls are attached. Otherwise this blocks
        on the menu until it stops or times out, then disables the controls.
        """
        if self._cv2:
            await ctx.respond(
                flags=h.MessageFlag.IS_COMPONENTS_V2,
                components=self._render_components(),
            )
        elif self.needs_pagination:
            await ctx.respond(embed=self._current_embed(), components=self._menu)
        else:
            await ctx.respond(embed=self._current_embed())

        if not self.needs_pagination:
            return

        # Capture the message id so we can disable the controls on timeout (there is
        # no MenuContext available outside a button press).
        self._ctx = ctx
        self._message = await ctx.interaction.fetch_initial_response()

        await self._menu.attach(ctx.client, timeout=self._timeout)
        # ``attach`` blocks until the menu stops or times out. A press never stops
        # the menu here, so reaching this point means it timed out: disable controls.
        await self._on_timeout()

    async def _edit(self, mctx: lbc.MenuContext, *, all_disabled: bool = False) -> None:
        if self._cv2:
            # ``respond(edit=True)`` edits the initial response in place and, unlike
            # ``edit_response``, accepts the CV2 flag.
            await mctx.respond(
                edit=True,
                flags=h.MessageFlag.IS_COMPONENTS_V2,
                components=self._render_components(all_disabled=all_disabled),
            )
        elif all_disabled:
            await mctx.respond(edit=True, embed=self._current_embed(), components=[])
        else:
            await mctx.respond(
                edit=True, embed=self._current_embed(), components=self._menu
            )

    # -- callbacks ---------------------------------------------------------

    async def _on_prev(self, mctx: lbc.MenuContext) -> None:
        if self._index > 0:
            self._index -= 1
        await self._edit(mctx)

    async def _on_next(self, mctx: lbc.MenuContext) -> None:
        if self._index < self.page_count - 1:
            self._index += 1
        await self._edit(mctx)

    async def _on_timeout(self) -> None:
        """Disable the controls on the displayed message after a menu timeout.

        ``edit_message`` does not take a flags argument; editing the components of a
        message that was created with ``IS_COMPONENTS_V2`` preserves that flag.
        """
        if self._ctx is None or self._message is None:
            return
        if self._cv2:
            await self._ctx.interaction.edit_message(
                self._message,
                components=self._render_components(all_disabled=True),
            )
        else:
            await self._ctx.interaction.edit_message(self._message, components=[])
