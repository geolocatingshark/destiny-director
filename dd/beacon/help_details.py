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

"""Per-command detailed ``/help`` pages for the beacon bot.

Each :class:`~dd.common.help.CommandDetail` is keyed by its command's registered name
and surfaced via ``/help command:<name>``. The mirror and ``/command`` entries are
admin-only (control-guild scoped), so they are suggested to the bot team only. (The
generic ``/help`` self-detail is added by the shared factory, not listed here.)
"""

from ..common.help import CommandDetail

MIRROR_SEND_DETAIL = CommandDetail(
    command="mirror_send",
    title="Mirror send (right-click message command)",
    summary=(
        "Manually mirror a source message out to its destination channels now, instead "
        "of waiting for the automatic trigger. Owner/team only."
    ),
    steps=(
        'Right-click (long-press on mobile) the source message ▸ Apps ▸ "mirror_send".',
        "The bot fans the message out to every destination following that source "
        "channel and reports progress.",
    ),
    notes=(
        "Use this when an automatic mirror didn't fire, or to back-fill a destination.",
        "Crossposting is not waited on for manual sends.",
    ),
)

MIRROR_UPDATE_DETAIL = CommandDetail(
    command="mirror_update",
    title="Mirror update (right-click message command)",
    summary=(
        "Re-sync an already-mirrored source message so its mirrored copies match the "
        "current source content. Owner/team only."
    ),
    steps=(
        "Right-click the original source message (the one already mirrored) ▸ Apps ▸ "
        '"mirror_update".',
        "The bot edits the mirrored copies in each destination to match the source.",
    ),
    notes=(
        "Use after editing a source message whose edit didn't propagate automatically.",
    ),
)

MIRROR_CANCEL_DETAIL = CommandDetail(
    command="mirror_cancel",
    title="Mirror cancel (right-click message command)",
    summary=(
        "Cancel a mirror operation that is currently in progress for a source message. "
        "Owner/team only."
    ),
    steps=(
        "Right-click the source message whose mirror is running ▸ Apps ▸ "
        '"mirror_cancel".',
        "The in-flight mirror for that message is cancelled.",
    ),
    notes=(
        "Fails with a message if no mirror is currently in progress for that message.",
        'You can also cancel from the "Cancel Mirror" button on the progress message.',
    ),
)

COMMAND_GROUP_DETAIL = CommandDetail(
    command="command",
    title="Custom commands (/command)",
    summary=(
        "Create and manage DB-backed custom commands that end users can run. "
        "Owner/team only."
    ),
    steps=(
        "`/command preview` — render a response without saving, to check it first.",
        "`/command add` — create a command: pick a type, give a description, name its "
        "layer(s), and provide the response.",
        "`/command edit` — change an existing command's type or response.",
        "`/command rename` — rename a command's layer(s).",
        "`/command delete` — remove a command.",
    ),
    notes=(
        "Layers nest commands into groups (e.g. layer1 group ▸ layer2 subcommand).",
        "Custom commands can't shadow code-defined commands of the same name.",
        "Changes sync to Discord; global propagation can take time to appear "
        "for users.",
    ),
)

HELP_DETAILS: tuple[CommandDetail, ...] = (
    MIRROR_SEND_DETAIL,
    MIRROR_UPDATE_DETAIL,
    MIRROR_CANCEL_DETAIL,
    COMMAND_GROUP_DETAIL,
)
