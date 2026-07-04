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
from ...common.components import build_container, embeds_to_container
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
class CreateEmbed(lb.SlashCommand, name="embed", description="Create a new embed post"):
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


class EditEmbed(lb.MessageCommand, name="Edit embed", description="Edit an embed post"):
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


class CopyEmbed(
    lb.MessageCommand,
    name="Copy embed",
    description="Copy, edit and then send an embed post",
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


@post_group.register
class PostComponentsFromLink(
    lb.SlashCommand,
    name="components",
    description="Post a Components V2 message from a discord.builders link or JSON",
):
    link = lb.string(
        "link",
        "A discord.builders link, bare hash, or raw JSON (leave blank for the "
        "editor link)",
        default="",
        max_length=6000,
    )
    channel = lb.channel(
        "channel",
        "Where to post it (defaults to this channel)",
        default=None,
        channel_types=[h.ChannelType.GUILD_TEXT, h.ChannelType.GUILD_NEWS],
    )

    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        # No link → hand back the editor link and how to use it.
        if not str(self.link).strip():
            await ctx.respond(
                embed=h.Embed(
                    title="Post a Components V2 message",
                    description=(
                        "1. Design your message on "
                        "[discord.builders](https://discord.builders).\n"
                        "2. Copy the page URL from your browser's address bar.\n"
                        "3. Re-run `/post components` with that URL in the `link` "
                        "option (add `channel:` to post elsewhere; defaults to this "
                        "channel)."
                    ),
                    color=_PROMPT_COLOR,
                ),
                ephemeral=True,
            )
            return

        try:
            components = extract_components_from_input(str(self.link))
        except ValueError as e:
            await ctx.respond(
                embed=_error_embed("Couldn't read that link", str(e)), ephemeral=True
            )
            return

        target_id = self.channel.id if self.channel else ctx.channel_id
        channel = t.cast(h.TextableChannel, await bot.fetch_channel(target_id))
        try:
            posted = await channel.send(components=components)
        except h.ForbiddenError:
            await ctx.respond(
                embed=_error_embed(
                    "Missing permissions",
                    f"I don't have permission to post in <#{target_id}>.",
                ),
                ephemeral=True,
            )
            return
        except h.BadRequestError as e:
            await ctx.respond(
                embed=_error_embed(
                    "Discord rejected the message",
                    "Make sure it's a valid Components V2 structure, within the "
                    f"4000-character text limit.\n```\n{e}\n```",
                ),
                ephemeral=True,
            )
            return

        link = posted.make_link(ctx.guild_id)
        await ctx.respond(
            embed=h.Embed(description=f"✅ Posted: {link}", color=_SUCCESS_COLOR),
            ephemeral=True,
        )


class PostComponents(
    lb.MessageCommand,
    name="Post components",
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
            "This only works on Components V2 posts. Use **Edit embed** for embed "
            "posts.",
            ephemeral=True,
        )
        return True
    return False


class EditComponents(
    lb.MessageCommand,
    name="Edit components",
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

        # Masked links render only inside an embed (not in plain message content), so
        # the editor link is surfaced via an embed description.
        if len(url) < 3500:  # embed descriptions allow up to 4096 chars
            step_1 = f"1. [Open the editor]({url}) (pre-loaded with this post)."
        else:
            step_1 = (
                "1. This post is too large to fit in a link — load the attached "
                "`post.json` into the editor instead."
            )
        description = (
            f"{step_1}\n"
            "2. Make your changes there, then copy the page URL.\n"
            "3. Run **Update components** on this same post and paste that URL "
            "(or the JSON)."
        )

        await ctx.respond(
            embed=h.Embed(
                title="Edit this Components V2 post",
                description=description,
                color=_PROMPT_COLOR,
            ),
            attachment=attachment,
            ephemeral=True,
        )


class _UpdateComponentsModal(lbc.Modal):
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


class UpdateComponents(
    lb.MessageCommand,
    name="Update components",
    description="Update this Components V2 post from a discord.builders link or JSON",
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        message = self.target
        if await _reject_unless_own_cv2(ctx, message, bot):
            return

        modal = _UpdateComponentsModal(message)
        custom_id = str(uuid.uuid4())
        await ctx.respond_with_modal("Update components", custom_id, components=modal)
        # ``attach`` blocks until the modal is submitted; the edit + feedback happen in
        # ``on_submit``. A timeout just means the user dismissed it — nothing to do.
        with contextlib.suppress(asyncio.TimeoutError):
            await modal.attach(ctx.client, custom_id, timeout=600)


class ConvertToComponents(
    lb.MessageCommand,
    name="Convert to components",
    description="Convert this embed post into a Components V2 message in place",
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        message = self.target

        bot_user = bot.get_me()
        if not (bot_user and message.author.id == bot_user.id):
            await ctx.respond(
                "Can only convert messages posted by this bot", ephemeral=True
            )
            return

        if _is_cv2(message):
            await ctx.respond(
                embed=_error_embed(
                    "Already Components V2",
                    "This post is already a Components V2 message. Use **Edit "
                    "components** to change it.",
                ),
                ephemeral=True,
            )
            return

        if not message.embeds:
            await ctx.respond(
                embed=_error_embed(
                    "Nothing to convert", "This message has no embed to convert."
                ),
                ephemeral=True,
            )
            return

        container = embeds_to_container(message.embeds)
        if not container.components:
            await ctx.respond(
                embed=_error_embed(
                    "Nothing to convert",
                    "This message's embed has no content I can turn into components.",
                ),
                ephemeral=True,
            )
            return

        # Preview + confirm. The preview is itself a CV2 message so its flag matches
        # the final result; the confirm/cancel controls route through an ``lbc.Menu``
        # but are rendered as a separate top-level action row so the previewed container
        # is identical to what gets posted. Every terminal edit stays CV2 because a
        # message's ``IS_COMPONENTS_V2`` flag can't be removed by a later edit.
        confirm_id = "dd_convert:confirm"
        cancel_id = "dd_convert:cancel"
        handled = False

        async def on_confirm(mctx: lbc.MenuContext) -> None:
            nonlocal handled
            handled = True
            try:
                await message.edit(
                    components=[container], flags=h.MessageFlag.IS_COMPONENTS_V2
                )
            except h.ForbiddenError:
                await mctx.respond(
                    edit=True,
                    flags=h.MessageFlag.IS_COMPONENTS_V2,
                    components=[
                        build_container(
                            ["⚠️ I don't have permission to edit that message."]
                        )
                    ],
                )
                mctx.stop_interacting()
                return
            except h.BadRequestError as e:
                rejected = f"⚠️ Discord rejected the converted message.\n```\n{e}\n```"
                await mctx.respond(
                    edit=True,
                    flags=h.MessageFlag.IS_COMPONENTS_V2,
                    components=[build_container([rejected])],
                )
                mctx.stop_interacting()
                return

            link = message.make_link(ctx.guild_id)
            await mctx.respond(
                edit=True,
                flags=h.MessageFlag.IS_COMPONENTS_V2,
                components=[build_container([f"✅ Converted: {link}"])],
            )
            mctx.stop_interacting()

        async def on_cancel(mctx: lbc.MenuContext) -> None:
            nonlocal handled
            handled = True
            await mctx.respond(
                edit=True,
                flags=h.MessageFlag.IS_COMPONENTS_V2,
                components=[
                    build_container(["Cancelled — the message was left unchanged."])
                ],
            )
            mctx.stop_interacting()

        menu = lbc.Menu()
        menu.add_interactive_button(
            h.ButtonStyle.SUCCESS, on_confirm, custom_id=confirm_id, label="Convert"
        )
        menu.add_interactive_button(
            h.ButtonStyle.SECONDARY, on_cancel, custom_id=cancel_id, label="Cancel"
        )

        controls = h.impl.MessageActionRowBuilder()
        controls.add_interactive_button(
            h.ButtonStyle.SUCCESS, confirm_id, label="Convert"
        )
        controls.add_interactive_button(
            h.ButtonStyle.SECONDARY, cancel_id, label="Cancel"
        )

        await ctx.respond(
            flags=h.MessageFlag.IS_COMPONENTS_V2,
            components=[container, controls],
            ephemeral=True,
        )

        with contextlib.suppress(TimeoutError):
            await menu.attach(ctx.client, timeout=300)

        if not handled:
            # Timed out with no choice — drop the now-dead controls and say so.
            with contextlib.suppress(h.HTTPResponseError):
                await ctx.interaction.edit_initial_response(
                    components=[
                        build_container(
                            ["⏱️ Timed out — nothing changed. Run the command again."]
                        )
                    ],
                )


# Post commands are usable in the Kyber server in addition to control + test_env (the
# slash /post group too, per request). The client-level owner hook still gates them to
# bot owners.
_post_guilds = utils.guild_scope(
    *cfg.test_env,
    cfg.control_discord_server_id,
    cfg.kyber_discord_server_id,
)
loader.command(post_group, guilds=_post_guilds)
loader.command(EditEmbed, guilds=_post_guilds)
loader.command(CopyEmbed, guilds=_post_guilds)
loader.command(ConvertToComponents, guilds=_post_guilds)
loader.command(PostComponents, guilds=_post_guilds)
loader.command(EditComponents, guilds=_post_guilds)
loader.command(UpdateComponents, guilds=_post_guilds)
