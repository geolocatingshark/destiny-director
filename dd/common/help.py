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

"""Shared, miru-free ``/help`` renderer for both bots.

Introspects the lightbulb v3 command client's registered commands, groups them into
user-facing categories, filters out administrative commands for non-team users, and
renders the result as a paginated Components V2 message via
:mod:`dd.common.components`.
"""

import typing as t

import hikari as h
import lightbulb as lb

from . import cfg, components
from .bot import CachedFetchBot

# Catch-all category title for loose/ungrouped commands. User-facing.
GENERAL_CATEGORY = "General Commands"

# Header shown at the top of the help message.
HELP_HEADING = "# Bot Help"

# Commands that are global (so not caught by the control-guild check) but should
# still only be visible to the bot team. Names are top-level command/group names.
ADMIN_ONLY_NAMES: frozenset[str] = frozenset({"testing"})

# lightbulb stores the global-command registration under this key in the client's
# ``_registered_commands`` guild sets.
_GLOBAL_COMMAND_KEY = 0


def _command_data(obj: t.Any) -> t.Any | None:
    """Return lightbulb's ``CommandData`` for a command class / group / subgroup.

    Read the name and description through ``_command_data`` rather than ``obj.name``
    / ``obj.description`` directly: a command that declares an option named ``name``
    or ``description`` shadows that attribute with lightbulb's option descriptor,
    which evaluates to the ``lightbulb.utils.EMPTY`` marker when accessed on the
    class (rendering as ``<lightbulb.Marker: 'EMPTY'>`` in the help text). Groups
    expose ``_command_data`` as a property and command classes as a class var, so
    this works uniformly for both.
    """
    return getattr(obj, "_command_data", None)


def _command_name(obj: t.Any) -> str:
    """Return the registered name of a command class / group / subgroup."""
    data = _command_data(obj)
    return str(getattr(data, "name", "") or "") if data is not None else ""


def _command_description(obj: t.Any) -> str:
    """Return the registered description of a command class / group / subgroup."""
    data = _command_data(obj)
    return str(getattr(data, "description", "") or "") if data is not None else ""


def _is_group(obj: t.Any) -> bool:
    """Whether ``obj`` is a (sub)group, i.e. exposes ``subcommands``."""
    return hasattr(obj, "subcommands")


def _enabled_guilds(client: lb.Client, command: t.Any) -> set[int]:
    """Return the set of guild ids a top-level command/group is registered to.

    Reads the client's ``_registered_commands`` mapping populated by ``register``.
    The mapping value is a set of guild snowflakes (with ``0`` meaning global) or the
    literal string ``"defer"`` when guild registration is deferred. Returns an empty
    set when the command is not present / deferred.
    """
    registered = getattr(client, "_registered_commands", {})
    guilds = registered.get(command)
    if not isinstance(guilds, (set, frozenset, list, tuple)):
        return set()
    return {int(g) for g in guilds}


def _is_admin_command(client: lb.Client, command: t.Any) -> bool:
    """Whether a top-level command/group is administrative (team-only).

    A command is admin if it is registered to the control discord server, or if its
    name is in the explicit :data:`ADMIN_ONLY_NAMES` deny-set. Control-guild
    membership is checked specifically (not "is guild scoped") because in a test
    environment every command is guild-scoped.
    """
    if _command_name(command) in ADMIN_ONLY_NAMES:
        return True
    guilds = _enabled_guilds(client, command)
    return cfg.control_discord_server_id in guilds


def _format_command_line(qualified_name: str, description: str) -> str:
    """Format a single command help line as markdown."""
    description = description.strip()
    if description:
        return f"`/{qualified_name}` - {description}"
    return f"`/{qualified_name}`"


def _collect_subcommand_lines(
    group_or_subgroup: t.Any, parents: list[str]
) -> list[str]:
    """Recursively collect help lines for a group's subcommands and subgroups."""
    lines: list[str] = []
    subcommands: dict[str, t.Any] = getattr(group_or_subgroup, "subcommands", {})
    for child in subcommands.values():
        name = _command_name(child)
        if not name:
            continue
        if _is_group(child):
            # A subgroup: recurse, prefixing its name.
            lines.extend(_collect_subcommand_lines(child, parents + [name]))
        else:
            qualified = " ".join(parents + [name])
            lines.append(_format_command_line(qualified, _command_description(child)))
    return lines


def group_commands(client: lb.Client, *, is_admin: bool) -> dict[str, list[str]]:
    """Group registered commands into user-facing categories of help lines.

    Each top-level :class:`lb.Group` becomes its own category (titled from the group
    name); loose/ungrouped commands fall under :data:`GENERAL_CATEGORY`. When
    ``is_admin`` is ``False`` administrative commands (whole groups and individual
    commands) are omitted. Categories with no visible commands are dropped.

    All visible commands are treated uniformly: nothing is special-cased or labelled
    by how it is backed.
    """
    categories: dict[str, list[str]] = {}

    for command in client.registered_commands:
        name = _command_name(command)
        if not name:
            continue

        if not is_admin and _is_admin_command(client, command):
            continue

        if _is_group(command):
            # A top-level group -> its own category named after the group.
            title = name.replace("_", " ").capitalize()
            lines = _collect_subcommand_lines(command, [name])
            if lines:
                categories.setdefault(title, []).extend(lines)
        else:
            # A loose command -> General Commands.
            line = _format_command_line(name, _command_description(command))
            categories.setdefault(GENERAL_CATEGORY, []).append(line)

    # Sort lines within each category for stable, readable output. Order General
    # Commands last so grouped categories appear first.
    ordered: dict[str, list[str]] = {}
    for title in sorted(categories, key=lambda t_: (t_ == GENERAL_CATEGORY, t_)):
        ordered[title] = sorted(categories[title])
    return ordered


def _category_section(title: str, lines: t.Sequence[str]) -> str:
    """Render a category as a markdown section (heading + command lines)."""
    return f"### {title}\n" + "\n".join(lines)


def _paginate_sections(
    categories: dict[str, list[str]], *, title: str
) -> list[list[str]]:
    """Split categories into pages, each a list of markdown section strings.

    The first text display of every page is the help heading. Categories are kept
    whole where possible; a category whose lines exceed the per-page limits is split
    across pages (its title repeated) via the char/line chunker in
    :mod:`dd.common.components`.
    """
    pages: list[list[str]] = []
    current: list[str] = []
    current_len = len(title)

    def flush() -> None:
        nonlocal current, current_len
        if current:
            pages.append(current)
        current = []
        current_len = len(title)

    for cat_title, lines in categories.items():
        # Chunk this category's lines into page-sized blocks.
        for block in components.chunk_lines_to_sections(lines):
            section = _category_section(cat_title, block.split("\n"))
            if current and current_len + len(section) > components.MAX_PAGE_CHARS:
                flush()
            current.append(section)
            current_len += len(section)

    flush()
    return pages or [[]]


def _make_page_factory(
    sections: list[str], *, color: h.Color
) -> components.Cv2PageFactory:
    """Build a CV2 page factory rendering the heading + the given sections."""

    def factory() -> list[h.api.ComponentBuilder]:
        return [
            components.build_container([HELP_HEADING, *sections], accent_color=color)
        ]

    return factory


async def render_help(
    ctx: lb.Context,
    *,
    title: str = HELP_HEADING,
    color: h.Color = cfg.embed_default_color,
    is_admin: bool = False,
) -> None:
    """Render and send the paginated ``/help`` message.

    Introspects ``ctx.client``'s registered commands, groups them into categories,
    filters administrative commands when ``not is_admin``, and sends a paginated
    Components V2 message (a single page when everything fits), attaching the
    paginator's controls.
    """
    categories = group_commands(ctx.client, is_admin=is_admin)

    if not categories:
        await ctx.respond(
            flags=h.MessageFlag.IS_COMPONENTS_V2,
            components=[
                components.build_container(
                    [title, "No commands available."], accent_color=color
                )
            ],
        )
        return

    section_pages = _paginate_sections(categories, title=title)
    pages: list[components.Page] = [
        _make_page_factory(sections, color=color) for sections in section_pages
    ]

    paginator = components.Paginator(pages)
    await paginator.send(ctx)


def make_help_command() -> type[lb.SlashCommand]:
    """Builds a fresh ``/help`` command class for a bot's loader."""

    class Help(
        lb.SlashCommand, name="help", description="Get help information for the bot"
    ):
        @lb.invoke
        async def invoke(
            self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED
        ) -> None:
            is_admin = ctx.user.id in await bot.fetch_owner_ids()
            await render_help(ctx, is_admin=is_admin)

    return Help
