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

"""Per-command detailed ``/help`` pages for the anchor bot.

Each :class:`~dd.common.help.CommandDetail` is keyed by its command's registered name
and surfaced via ``/help command:<name>``. Anchor is owner-only, so these are visible to
the bot team only.
"""

from ..common.help import HELP_SELF_DETAIL, CommandDetail

POST_JSON_DETAIL = CommandDetail(
    command="Post as JSON",
    title="Post as JSON (right-click message command)",
    summary=(
        "Posts a Components V2 message to an announcement channel from JSON you "
        "designed in an external builder. Owner/team only."
    ),
    steps=(
        "Build your message in a Components V2 builder site (e.g. message.style or "
        "discord.builders) and copy its exported JSON.",
        "Send that JSON to a channel the bot can see — paste it as a normal message, "
        "or attach it as a `.json`/`.txt` file (Discord auto-files very long pastes).",
        'Right-click (long-press on mobile) that message ▸ Apps ▸ "Post as JSON".',
        "In the ephemeral prompt, pick the destination announcement channel (only "
        "announcement channels are listed).",
        "The bot posts the message verbatim, replies with a link, and deletes your "
        "original JSON message.",
    ),
    notes=(
        "JSON may be a full message object, a bare components array, or a single "
        "component object.",
        '"No JSON found" → the targeted message had no readable text/attachment.',
        '"Invalid JSON" → the parser rejected it; check it is valid Components V2.',
        "No post permission in the chosen channel → it reports and posts nothing.",
        "Posted but no Manage Messages → it links the post and asks you to delete the "
        "source yourself.",
        "The channel picker times out after 5 minutes; just re-run.",
    ),
)

CREATE_POST_DETAIL = CommandDetail(
    command="post",
    title="Create a post (/post create)",
    summary=(
        "Build an embed from scratch with the interactive builder and post it to the "
        "current channel. Owner/team only."
    ),
    steps=(
        "Run `/post create` in the channel you want to post to.",
        "Fill in the embed via the ephemeral builder buttons (title, text, colour, "
        "author, image, thumbnail, footer).",
        'Press "Post" to send the embed to the current channel.',
    ),
    notes=(
        "If the bot cannot post in the channel you get a ForbiddenError message.",
        "Oversized embeds/attachments are rejected by Discord with a BadRequestError.",
    ),
)

EDIT_POST_DETAIL = CommandDetail(
    command="edit",
    title="Edit (right-click message command)",
    summary=(
        "Edit an embed the bot already posted, using the interactive embed builder. "
        "Owner/team only."
    ),
    steps=(
        'Right-click (long-press on mobile) a message this bot posted ▸ Apps ▸ "edit".',
        "Change the title, text, colour, author, image, thumbnail or footer in the "
        "ephemeral builder.",
        'Press "Edit" to apply your changes to the original message in place.',
    ),
    notes=(
        "Only works on messages posted by this bot.",
        "The message must contain exactly one embed.",
    ),
)

COPY_POST_DETAIL = CommandDetail(
    command="copy",
    title="Copy (right-click message command)",
    summary=(
        "Clone any single-embed message, tweak it in the builder, then post it fresh "
        "in the current channel. Owner/team only."
    ),
    steps=(
        'Right-click any message that has one embed ▸ Apps ▸ "copy".',
        "Adjust the copied embed in the ephemeral builder.",
        'Press "Send" to post the result as a new message in the channel you ran the '
        "command in.",
    ),
    notes=(
        "The source message must contain exactly one embed.",
        "Unlike Edit, this posts a new message and leaves the original untouched.",
    ),
)

LS_UPDATE_DETAIL = CommandDetail(
    command="ls_update",
    title="Update lost sector post (right-click message command)",
    summary=(
        "Re-render an existing lost sector announcement with the current day's data, "
        "fixing a stale or wrong post. Owner/team only."
    ),
    steps=(
        'Right-click the lost sector announcement message ▸ Apps ▸ "ls_update".',
        "The bot rebuilds today's lost sector post and edits the message in place.",
    ),
    notes=(
        "Lost sector autoposts must be enabled first, or the command refuses to run.",
    ),
)

HELP_DETAILS: tuple[CommandDetail, ...] = (
    HELP_SELF_DETAIL,
    POST_JSON_DETAIL,
    CREATE_POST_DETAIL,
    EDIT_POST_DETAIL,
    COPY_POST_DETAIL,
    LS_UPDATE_DETAIL,
)
