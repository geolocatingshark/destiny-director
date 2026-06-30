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

import asyncio
import contextlib
import json
import logging
import typing as t
import uuid

import hikari as h
import lightbulb as lb
from lightbulb import components as lbc

from ...common import cfg, utils
from ...common.bot import CachedFetchBot
from ..builders_link import (
    builders_url,
    extract_components_from_input,
    fetch_raw_message_components,
)
from ..embeds import build_embed_with_user
from ..post_json import parse_post_json

loader = lb.Loader()

post_group = lb.Group("post", "Post management commands")


@post_group.register
class CreatePost(lb.SlashCommand, name="create", description="Create a new post"):
    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        embed = await build_embed_with_user(ctx, done_button_text="Post")
        if embed is None:
            return

        initial = await ctx.respond("Posting...", ephemeral=True)
        channel = t.cast(h.TextableChannel, await bot.fetch_channel(ctx.channel_id))
        try:
            await channel.send(embed)
        except h.ForbiddenError as e:
            await ctx.edit_response(
                initial,
                "**ForbiddenError**: It looks like I do not have permission to "
                "send messages here",
            )
            logging.exception(e)
        except h.BadRequestError as e:
            await ctx.edit_response(
                initial,
                "**BadRequestError**: It looks like the embed is either too large, has "
                + "too many attachments, has attachments that are too large, or has "
                + "exceeded some other limit. See description from documentation below:"
                + "\n"
                + "```\n"
                + "This may be raised in several discrete situations, such as messages "
                + "being empty with no attachments or embeds; messages with more than "
                + "2000 characters in them, embeds that exceed one of the many embed "
                + "limits; too many attachments; attachments that are too "
                + "large; invalid "
                + "image URLs in embeds; reply not found or not in the same "
                + "channel; too "
                + "many components.\n"
                + "```\n",
            )
            logging.exception(e)


class EditPost(lb.MessageCommand, name="edit", description="Edit a post"):
    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        message = self.target

        bot_user = bot.get_me()
        if not (bot_user and message.author.id == bot_user.id):
            await ctx.respond(
                "Can only edit messages posted by this bot", ephemeral=True
            )
            return

        if not (message.embeds and len(message.embeds) == 1):
            await ctx.respond("Can only edit messages with 1 embed", ephemeral=True)
            return

        embed = await build_embed_with_user(
            ctx, done_button_text="Edit", existing_embed=message.embeds[0]
        )
        if embed is None:
            return

        await message.edit(embed=embed)


class CopyPost(
    lb.MessageCommand, name="copy", description="Copy, edit and then send a post"
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        message = self.target

        if not (message.embeds and len(message.embeds) == 1):
            await ctx.respond("Can only edit messages with 1 embed", ephemeral=True)
            return

        embed = await build_embed_with_user(
            ctx, done_button_text="Send", existing_embed=message.embeds[0]
        )
        if embed is None:
            return

        channel = t.cast(h.TextableChannel, await bot.fetch_channel(ctx.channel_id))
        await channel.send(embed=embed)


_ERROR_COLOR = h.Color(0xED4245)  # Discord "danger" red
_SUCCESS_COLOR = h.Color(0x57F287)  # Discord "green"
_PROMPT_COLOR = h.Color(0x5865F2)  # Discord "blurple"

# Posts may only target announcement channels.
_POSTABLE_CHANNEL_TYPES = (h.ChannelType.GUILD_NEWS,)


def _error_embed(title: str, description: str) -> h.Embed:
    return h.Embed(title=f"⚠️ {title}", description=description, color=_ERROR_COLOR)


async def _extract_json_source(message: h.Message) -> str | None:
    """Return the JSON carried by a message.

    Prefers a text attachment (Discord auto-files pasted text over ~2000 chars as a
    ``message.txt``), and otherwise falls back to the inline content so mobile users who
    paste JSON directly are also supported.
    """
    for attachment in message.attachments:
        media_type = attachment.media_type or ""
        if media_type.startswith("text") or attachment.filename.endswith(
            (".json", ".txt")
        ):
            return (await attachment.read()).decode("utf-8", errors="replace")
    return message.content or None


async def _post_to_selected_channel(
    mctx: lbc.MenuContext,
    bot: CachedFetchBot,
    components: t.Sequence[h.api.ComponentBuilder],
    source: h.Message,
) -> None:
    """Post the components to the channel chosen in the select menu, then clean up.

    All feedback edits the original ephemeral picker message (embeds only, so nothing
    stale lingers).
    """
    channel_id = h.Snowflake(mctx.interaction.values[0])
    channel = t.cast(h.TextableChannel, await bot.fetch_channel(channel_id))

    try:
        posted = await channel.send(components=components)
    except h.ForbiddenError:
        await mctx.respond(
            embed=_error_embed(
                "Missing permissions",
                f"I do not have permission to post in <#{channel_id}>.",
            ),
            components=[],
            edit=True,
        )
        return
    except h.BadRequestError as e:
        await mctx.respond(
            embed=_error_embed(
                "Discord rejected the message",
                "Make sure the JSON is a valid Components V2 structure, within the "
                f"4000-character text limit.\n```\n{e}\n```",
            ),
            components=[],
            edit=True,
        )
        return

    link = posted.make_link(mctx.guild_id)
    try:
        await source.delete()
    except h.ForbiddenError:
        await mctx.respond(
            embed=_error_embed(
                "Posted — but couldn't clean up",
                f"Posted: {link}\n\nPlease grant me **Manage Messages** so I can "
                "delete your source message automatically.",
            ),
            components=[],
            edit=True,
        )
        return

    await mctx.respond(
        embed=h.Embed(description=f"✅ Posted: {link}", color=_SUCCESS_COLOR),
        components=[],
        edit=True,
    )


class PostJson(
    lb.MessageCommand,
    name="Post as JSON",
    description="Post a Components V2 message built from this message's JSON",
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        source = self.target

        raw = await _extract_json_source(source)
        if raw is None:
            await ctx.respond(
                embed=_error_embed(
                    "No JSON found",
                    "Paste the JSON as a message, or attach it as a `.json`/`.txt` "
                    "file, then run this command on that message.",
                ),
                ephemeral=True,
            )
            return

        try:
            components = parse_post_json(raw)
        except ValueError as e:
            await ctx.respond(
                embed=_error_embed("Invalid JSON", str(e)), ephemeral=True
            )
            return

        menu = lbc.Menu()

        async def on_select(mctx: lbc.MenuContext) -> None:
            await _post_to_selected_channel(mctx, bot, components, source)
            mctx.stop_interacting()

        menu.add_channel_select(
            on_select,
            placeholder="Select an announcement channel to post to",
            channel_types=_POSTABLE_CHANNEL_TYPES,
        )

        await ctx.respond(
            embed=h.Embed(
                description="Select an announcement channel to post this message to.",
                color=_PROMPT_COLOR,
            ),
            components=menu,
            ephemeral=True,
        )
        try:
            await menu.attach(ctx.client, timeout=300)
        except TimeoutError:
            await ctx.interaction.edit_initial_response(
                embed=_error_embed(
                    "Timed out", "No channel selected — please run the command again."
                ),
                components=[],
            )


def _is_cv2(msg: h.Message) -> bool:
    """Whether a message was sent as a Components V2 message."""
    return h.MessageFlag.IS_COMPONENTS_V2 in (msg.flags or h.MessageFlag.NONE)


async def _reject_unless_own_cv2(
    ctx: lb.Context, message: h.Message, bot: CachedFetchBot
) -> bool:
    """Respond + return ``True`` if ``message`` isn't an editable bot CV2 message."""
    bot_user = bot.get_me()
    if not (bot_user and message.author.id == bot_user.id):
        await ctx.respond("Can only edit messages posted by this bot", ephemeral=True)
        return True
    if not _is_cv2(message):
        await ctx.respond(
            "This only works on Components V2 posts. Use **edit** for embed posts.",
            ephemeral=True,
        )
        return True
    return False


class EditInBuilder(
    lb.MessageCommand,
    name="Edit in builder",
    description="Get a discord.builders link + JSON to edit this Components V2 post",
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        message = self.target
        if await _reject_unless_own_cv2(ctx, message, bot):
            return

        try:
            components = await fetch_raw_message_components(
                message.channel_id, message.id
            )
        except Exception as e:
            logging.exception(e)
            await ctx.respond(
                embed=_error_embed(
                    "Couldn't read this post",
                    "I failed to fetch this message's components from Discord.",
                ),
                ephemeral=True,
            )
            return

        if not components:
            await ctx.respond(
                "This message has no Components V2 content to edit", ephemeral=True
            )
            return

        url = builders_url(components)
        attachment = h.Bytes(
            json.dumps({"components": components}, indent=2).encode("utf-8"),
            "post.json",
        )

        lines = ["**Edit this Components V2 post**", ""]
        if len(url) < 1900:  # leave headroom under Discord's 2000-char content limit
            lines.append(f"1. Open the builder (pre-loaded): {url}")
        else:
            lines.append(
                "1. This post is too large to fit in a link — load the attached "
                "`post.json` into your builder instead."
            )
        lines.append("2. Make your changes there, then copy the page URL.")
        lines.append(
            "3. Run **Update from builder** on this same post and paste that URL "
            "(or the JSON)."
        )

        await ctx.respond("\n".join(lines), attachment=attachment, ephemeral=True)


class _UpdateFromBuilderModal(lbc.Modal):
    """Modal collecting a discord.builders URL / JSON and applying it to ``message``."""

    def __init__(self, message: h.Message) -> None:
        self._message = message
        self.payload = self.add_paragraph_text_input(
            "discord.builders URL or component JSON",
            placeholder="Paste the discord.builders link (or raw component JSON)…",
            max_length=4000,
            required=True,
        )

    async def on_submit(self, ctx: lbc.ModalContext) -> None:
        raw = ctx.value_for(self.payload) or ""
        try:
            components = extract_components_from_input(raw)
        except ValueError as e:
            await ctx.respond(
                embed=_error_embed("Couldn't read that", str(e)), ephemeral=True
            )
            return

        try:
            await self._message.edit(
                components=components, flags=h.MessageFlag.IS_COMPONENTS_V2
            )
        except h.BadRequestError as e:
            await ctx.respond(
                embed=_error_embed(
                    "Discord rejected the update",
                    "Make sure it's a valid Components V2 structure, within the "
                    f"4000-character text limit.\n```\n{e}\n```",
                ),
                ephemeral=True,
            )
            return

        link = self._message.make_link(ctx.guild_id)
        await ctx.respond(
            embed=h.Embed(description=f"✅ Updated: {link}", color=_SUCCESS_COLOR),
            ephemeral=True,
        )


class UpdateFromBuilder(
    lb.MessageCommand,
    name="Update from builder",
    description="Update this Components V2 post from a discord.builders link or JSON",
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        message = self.target
        if await _reject_unless_own_cv2(ctx, message, bot):
            return

        modal = _UpdateFromBuilderModal(message)
        custom_id = str(uuid.uuid4())
        await ctx.respond_with_modal(
            "Update post from builder", custom_id, components=modal
        )
        # ``attach`` blocks until the modal is submitted; the edit + feedback happen in
        # ``on_submit``. A timeout just means the user dismissed it — nothing to do.
        with contextlib.suppress(asyncio.TimeoutError):
            await modal.attach(ctx.client, custom_id, timeout=600)


# Post commands are usable in the Kyber server in addition to control + test_env (the
# slash /post group too, per request). The client-level owner hook still gates them to
# bot owners.
_post_guilds = utils.guild_scope(
    *cfg.test_env,
    cfg.control_discord_server_id,
    cfg.kyber_discord_server_id,
)
loader.command(post_group, guilds=_post_guilds)
loader.command(EditPost, guilds=_post_guilds)
loader.command(CopyPost, guilds=_post_guilds)
loader.command(PostJson, guilds=_post_guilds)
loader.command(EditInBuilder, guilds=_post_guilds)
loader.command(UpdateFromBuilder, guilds=_post_guilds)
