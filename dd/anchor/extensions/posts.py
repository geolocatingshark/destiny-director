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

import contextlib
import logging
import typing as t

import hikari as h
import lightbulb as lb
from lightbulb import components as lbc

from ...common import cfg, utils
from ...common.bot import CachedFetchBot
from ...common.components import build_container, embeds_to_container
from ..cv2_builder import build_components_with_user
from ..cv2_nodes import Node
from ..cv2_raw import RawComponentBuilder, fetch_raw_message_components
from ..embeds import build_embed_with_user

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

# Discord rejects a Components V2 message longer than 4000 characters of text.
_CV2_LIMIT_HINT = (
    "Make sure it's a valid Components V2 structure, within the 4000-character "
    "text limit.\n```\n{error}\n```"
)


def _error_embed(title: str, description: str) -> h.Embed:
    return h.Embed(title=f"⚠️ {title}", description=description, color=_ERROR_COLOR)


def _to_builders(nodes: list[Node]) -> list[RawComponentBuilder]:
    """Wrap raw component dicts into sendable builders (auto-sets the CV2 flag)."""
    return [RawComponentBuilder(node) for node in nodes]


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


async def _load_cv2_nodes(ctx: lb.Context, message: h.Message) -> list[Node] | None:
    """Fetch a CV2 post's raw component nodes, responding with an error on failure."""
    try:
        nodes = await fetch_raw_message_components(message.channel_id, message.id)
    except Exception as e:
        logging.exception(e)
        await ctx.respond(
            embed=_error_embed(
                "Couldn't read this post",
                "I failed to fetch this message's components from Discord.",
            ),
            ephemeral=True,
        )
        return None
    if not nodes:
        await ctx.respond(
            "This message has no Components V2 content to edit", ephemeral=True
        )
        return None
    return nodes


@post_group.register
class PostComponents(
    lb.SlashCommand,
    name="components",
    description="Build and post a Components V2 message in Discord",
):
    channel = lb.channel(
        "channel",
        "Where to post it (defaults to this channel)",
        default=None,
        channel_types=[h.ChannelType.GUILD_TEXT, h.ChannelType.GUILD_NEWS],
    )

    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        nodes = await build_components_with_user(ctx, done_button_text="Post")
        if not nodes:
            return

        target_id = self.channel.id if self.channel else ctx.channel_id
        channel = t.cast(h.TextableChannel, await bot.fetch_channel(target_id))
        try:
            posted = await channel.send(components=_to_builders(nodes))
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
                    "Discord rejected the message", _CV2_LIMIT_HINT.format(error=e)
                ),
                ephemeral=True,
            )
            return

        link = posted.make_link(ctx.guild_id)
        await ctx.respond(
            embed=h.Embed(description=f"✅ Posted: {link}", color=_SUCCESS_COLOR),
            ephemeral=True,
        )


class EditComponents(
    lb.MessageCommand,
    name="Edit components",
    description="Edit this Components V2 post in Discord",
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        message = self.target
        if await _reject_unless_own_cv2(ctx, message, bot):
            return

        nodes = await _load_cv2_nodes(ctx, message)
        if nodes is None:
            return

        edited = await build_components_with_user(
            ctx, done_button_text="Save", existing_nodes=nodes
        )
        if not edited:
            return

        try:
            await message.edit(
                components=_to_builders(edited), flags=h.MessageFlag.IS_COMPONENTS_V2
            )
        except h.BadRequestError as e:
            await ctx.respond(
                embed=_error_embed(
                    "Discord rejected the update", _CV2_LIMIT_HINT.format(error=e)
                ),
                ephemeral=True,
            )
            return

        link = message.make_link(ctx.guild_id)
        await ctx.respond(
            embed=h.Embed(description=f"✅ Updated: {link}", color=_SUCCESS_COLOR),
            ephemeral=True,
        )


class CopyComponents(
    lb.MessageCommand,
    name="Copy components",
    description="Copy, edit and then send a Components V2 post",
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        message = self.target
        if not _is_cv2(message):
            await ctx.respond("This only works on Components V2 posts.", ephemeral=True)
            return

        nodes = await _load_cv2_nodes(ctx, message)
        if nodes is None:
            return

        edited = await build_components_with_user(
            ctx, done_button_text="Send", existing_nodes=nodes
        )
        if not edited:
            return

        channel = t.cast(h.TextableChannel, await bot.fetch_channel(ctx.channel_id))
        try:
            posted = await channel.send(components=_to_builders(edited))
        except h.BadRequestError as e:
            await ctx.respond(
                embed=_error_embed(
                    "Discord rejected the message", _CV2_LIMIT_HINT.format(error=e)
                ),
                ephemeral=True,
            )
            return

        link = posted.make_link(ctx.guild_id)
        await ctx.respond(
            embed=h.Embed(description=f"✅ Posted: {link}", color=_SUCCESS_COLOR),
            ephemeral=True,
        )


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
                # Clear the embeds (and any content) in the same edit: a Components V2
                # message can't also carry embeds/content, and an unspecified field
                # would leave the original embed in place and be rejected.
                await message.edit(
                    content=None,
                    embeds=[],
                    components=[container],
                    flags=h.MessageFlag.IS_COMPONENTS_V2,
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
loader.command(EditComponents, guilds=_post_guilds)
loader.command(CopyComponents, guilds=_post_guilds)
