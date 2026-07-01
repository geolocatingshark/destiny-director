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

from ..common.help import CommandDetail

POST_JSON_DETAIL = CommandDetail(
    command="Post components",
    title="Post components (/post components or right-click)",
    summary=(
        "Posts a Components V2 message you designed in an external builder — from a "
        "discord.builders link via `/post components`, or from JSON on a message you "
        "right-click. Owner/team only."
    ),
    steps=(
        "Fastest: build it on discord.builders, copy the page URL, then run "
        "`/post components link:<url>` (defaults to the current channel; pass "
        "`channel:` to target another).",
        "Or, to post JSON: build your message in a Components V2 builder and copy its "
        "exported JSON.",
        "Send that JSON to a channel the bot can see — paste it as a normal message, "
        "or attach it as a `.json`/`.txt` file (Discord auto-files very long pastes).",
        'Right-click (long-press on mobile) that message ▸ Apps ▸ "Post components".',
        "In the ephemeral prompt, pick the destination announcement channel (only "
        "announcement channels are listed).",
        "The bot posts the message verbatim, replies with a link, and deletes your "
        "original JSON message.",
    ),
    notes=(
        "`/post components` accepts a discord.builders link, a bare hash, or raw JSON; "
        "very large links can exceed the 6000-char slash limit — use the right-click "
        "flow (or a `.json` attachment) for those.",
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
    title="Create an embed (/post embed)",
    summary=(
        "Build an embed from scratch with the interactive builder and post it to the "
        "current channel. Owner/team only."
    ),
    steps=(
        "Run `/post embed` in the channel you want to post to.",
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
    command="Edit embed",
    title="Edit embed (right-click message command)",
    summary=(
        "Edit an embed the bot already posted, using the interactive embed builder. "
        "Owner/team only."
    ),
    steps=(
        "Right-click (long-press on mobile) a message this bot posted ▸ Apps ▸ "
        '"Edit embed".',
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
    command="Copy embed",
    title="Copy embed (right-click message command)",
    summary=(
        "Clone any single-embed message, tweak it in the builder, then post it fresh "
        "in the current channel. Owner/team only."
    ),
    steps=(
        'Right-click any message that has one embed ▸ Apps ▸ "Copy embed".',
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

EDIT_COMPONENTS_DETAIL = CommandDetail(
    command="Edit components",
    title="Edit components (right-click message command)",
    summary=(
        "Edit a Components V2 post this bot made by round-tripping it through the "
        "discord.builders editor. Owner/team only."
    ),
    steps=(
        "Right-click (long-press on mobile) a Components V2 message this bot posted ▸ "
        'Apps ▸ "Edit components".',
        "Open the masked editor link in the reply — discord.builders loads pre-filled "
        "with the post.",
        "Make your changes there, then copy the page URL from your browser.",
        'Run "Update components" on the same message and paste that URL to apply it.',
    ),
    notes=(
        'Only works on Components V2 messages this bot posted (use "Edit embed" for '
        "embed posts).",
        "A `post.json` attachment is included as a fallback when the link is too long.",
    ),
)

UPDATE_COMPONENTS_DETAIL = CommandDetail(
    command="Update components",
    title="Update components (right-click message command)",
    summary=(
        "Apply an edited discord.builders design (or raw component JSON) back onto an "
        "existing Components V2 post. Owner/team only."
    ),
    steps=(
        'Right-click the Components V2 message to update ▸ Apps ▸ "Update components".',
        "Paste the discord.builders page URL (or raw component JSON) into the modal.",
        "The bot edits the message in place with your changes.",
    ),
    notes=(
        'Pair this with "Edit components", which gives you the pre-filled editor link.',
        "Invalid input or an oversized result is reported without changing the post.",
    ),
)

HELP_DETAILS: tuple[CommandDetail, ...] = (
    POST_JSON_DETAIL,
    CREATE_POST_DETAIL,
    EDIT_POST_DETAIL,
    COPY_POST_DETAIL,
    EDIT_COMPONENTS_DETAIL,
    UPDATE_COMPONENTS_DETAIL,
    LS_UPDATE_DETAIL,
)
