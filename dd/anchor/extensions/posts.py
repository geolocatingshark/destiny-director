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
from ...common.components import (
    CV2_NEUTRAL_COLOR,
    cv2_error,
    cv2_notice,
    cv2_success,
    embeds_to_container,
)
from ...common.schemas import Cv2Draft
from ..cv2_nodes import Node
from ..cv2_raw import RawComponentBuilder, fetch_raw_message_components
from ..embeds import build_embed_with_user
from .cv2_builder_page import new_draft

loader = lb.Loader()

post_group = lb.Group("post", "Post management commands")


@post_group.register
class CreateEmbed(lb.SlashCommand, name="embed", description="Create a new embed post"):
    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        embed = await build_embed_with_user(ctx, done_button_text="Post")
        if embed is None:
            return

        await _respond_cv2(ctx, cv2_notice("Posting…"))
        channel = t.cast(h.TextableChannel, await bot.fetch_channel(ctx.channel_id))
        try:
            await channel.send(embed)
        except h.ForbiddenError as e:
            await ctx.interaction.edit_initial_response(
                components=[
                    cv2_error(
                        "Missing permissions",
                        "It looks like I don't have permission to send messages here.",
                    )
                ],
            )
            logging.exception(e)
        except h.BadRequestError as e:
            await ctx.interaction.edit_initial_response(
                components=[
                    cv2_error(
                        "Discord rejected the embed",
                        "The embed may be empty, too large, have too many or "
                        "oversized attachments, or exceed another Discord limit.\n"
                        "```\n" + str(e) + "\n```",
                    )
                ],
            )
            logging.exception(e)


# Discord rejects a Components V2 message longer than 4000 characters of text.
_CV2_LIMIT_HINT = (
    "Make sure it's a valid Components V2 structure, within the 4000-character "
    "text limit.\n```\n{error}\n```"
)


async def _respond_cv2(
    ctx: lb.Context, container: h.impl.ContainerComponentBuilder
) -> None:
    """Send an ephemeral Components V2 status response (error/success/notice)."""
    await ctx.respond(
        components=[container],
        flags=h.MessageFlag.IS_COMPONENTS_V2,
        ephemeral=True,
    )


def _to_builders(nodes: list[Node]) -> list[RawComponentBuilder]:
    """Wrap raw component dicts into sendable builders (auto-sets the CV2 flag)."""
    return [RawComponentBuilder(node) for node in nodes]


def _is_cv2(msg: h.Message) -> bool:
    """Whether a message was sent as a Components V2 message."""
    return h.MessageFlag.IS_COMPONENTS_V2 in (msg.flags or h.MessageFlag.NONE)


# --- web handoff --------------------------------------------------------------------
#
# Components V2 authoring happens on the web (extensions.cv2_builder_page): a command
# writes a draft carrying the publish target and replies with a link. The in-Discord
# builder it replaced is gone — see plans/web_cv2_builder.md.
#
# The link is a plain link button, so posting is one click and no interaction
# round-trip.

_HANDOFF_BODY = {
    Cv2Draft.ACTION_POST: (
        "### Build your message\n"
        "Open the builder to write it — tap blocks to edit them, drag to rearrange, "
        "long-press (or right-click) for more.\n"
        "-# Your work saves as you go. Nothing is posted until you press **Post**."
    ),
    Cv2Draft.ACTION_EDIT: (
        "### Edit this message\n"
        "The builder opens with the current post loaded.\n"
        "-# Your work saves as you go. The live message only changes when you press "
        "**Save changes**."
    ),
    Cv2Draft.ACTION_COPY: (
        "### Copy this message\n"
        "The builder opens with a copy loaded — change whatever you like.\n"
        "-# Your work saves as you go. Nothing is sent until you press **Send copy**."
    ),
}


async def _hand_off_to_web(
    ctx: lb.Context,
    *,
    action: str,
    nodes: list[Node] | None,
    channel_id: int,
    message_id: int | None = None,
) -> None:
    """Reply with a link to the web builder for this draft."""
    if not cfg.public_base_url:
        # Only reachable on a local dev box with no tunnel; say so plainly rather than
        # handing out a link that cannot resolve.
        await _respond_cv2(
            ctx,
            cv2_error(
                "The web builder isn't reachable",
                "No public base URL is configured for this deployment, so I can't "
                "link you to the builder.",
            ),
        )
        return

    try:
        url = await new_draft(
            user_id=int(ctx.user.id),
            action=action,
            nodes=nodes,
            guild_id=int(ctx.guild_id) if ctx.guild_id else None,
            channel_id=channel_id,
            message_id=message_id,
        )
    except Exception as e:
        logging.exception(e)
        await _respond_cv2(
            ctx,
            cv2_error(
                "Couldn't start a draft",
                "Something went wrong opening the builder. It has been logged — "
                "try again in a moment.",
            ),
        )
        return

    container = h.impl.ContainerComponentBuilder(accent_color=CV2_NEUTRAL_COLOR)
    container.add_text_display(_HANDOFF_BODY[action])
    row = h.impl.MessageActionRowBuilder()
    row.add_component(h.impl.LinkButtonBuilder(url=url, label="Open the builder"))
    container.add_component(row)

    await ctx.respond(
        components=[container],
        flags=h.MessageFlag.IS_COMPONENTS_V2,
        ephemeral=True,
    )


def _is_own(message: h.Message, bot: CachedFetchBot) -> bool:
    """Whether ``message`` was posted by this bot."""
    bot_user = bot.get_me()
    return bool(bot_user and message.author.id == bot_user.id)


def _single_embed(message: h.Message) -> bool:
    """Whether ``message`` carries exactly one embed (the editable-embed shape)."""
    return bool(message.embeds and len(message.embeds) == 1)


async def _load_cv2_nodes(ctx: lb.Context, message: h.Message) -> list[Node] | None:
    """Fetch a CV2 post's raw component nodes, responding with an error on failure."""
    try:
        nodes = await fetch_raw_message_components(message.channel_id, message.id)
    except Exception as e:
        logging.exception(e)
        await _respond_cv2(
            ctx,
            cv2_error(
                "Couldn't read this post",
                "I failed to fetch this message's components from Discord.",
            ),
        )
        return None
    if not nodes:
        await _respond_cv2(
            ctx, cv2_error("This message has no Components V2 content to edit")
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
        target_id = int(self.channel.id if self.channel else ctx.channel_id)
        await _hand_off_to_web(
            ctx,
            action=Cv2Draft.ACTION_POST,
            nodes=None,
            channel_id=target_id,
        )


class EditPost(
    lb.MessageCommand,
    name="Edit post",
    description="Edit this bot's post (embed or Components V2)",
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        message = self.target
        if not _is_own(message, bot):
            await _respond_cv2(
                ctx, cv2_error("Can only edit messages posted by this bot")
            )
            return

        # Dispatch on the message's actual format — embeds and CV2 are both
        # first-class here; editing never crosses formats (that's what Convert is for).
        if _is_cv2(message):
            nodes = await _load_cv2_nodes(ctx, message)
            if nodes is None:
                return

            await _hand_off_to_web(
                ctx,
                action=Cv2Draft.ACTION_EDIT,
                nodes=nodes,
                channel_id=int(message.channel_id),
                message_id=int(message.id),
            )
            return

        if not _single_embed(message):
            await _respond_cv2(
                ctx,
                cv2_error(
                    "Nothing to edit",
                    "This post isn't a Components V2 message and doesn't have a "
                    "single embed to edit.",
                ),
            )
            return

        embed = await build_embed_with_user(
            ctx, done_button_text="Edit", existing_embed=message.embeds[0]
        )
        if embed is None:
            return

        await message.edit(embed=embed)


class CopyPost(
    lb.MessageCommand,
    name="Copy post",
    description="Copy, edit and then send a post (embed or Components V2)",
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        message = self.target

        # Copy preserves the source format — no ownership gate (any post is copyable).
        if _is_cv2(message):
            nodes = await _load_cv2_nodes(ctx, message)
            if nodes is None:
                return

            await _hand_off_to_web(
                ctx,
                action=Cv2Draft.ACTION_COPY,
                nodes=nodes,
                channel_id=int(ctx.channel_id),
            )
            return

        if not _single_embed(message):
            await _respond_cv2(
                ctx,
                cv2_error(
                    "Nothing to copy",
                    "This post isn't a Components V2 message and doesn't have a "
                    "single embed to copy.",
                ),
            )
            return

        embed = await build_embed_with_user(
            ctx, done_button_text="Send", existing_embed=message.embeds[0]
        )
        if embed is None:
            return

        channel = t.cast(h.TextableChannel, await bot.fetch_channel(ctx.channel_id))
        await channel.send(embed=embed)


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
            await _respond_cv2(
                ctx, cv2_error("Can only convert messages posted by this bot")
            )
            return

        if _is_cv2(message):
            await _respond_cv2(
                ctx,
                cv2_error(
                    "Already Components V2",
                    "This post is already a Components V2 message. Use **Edit post** "
                    "to change it.",
                ),
            )
            return

        if not message.embeds:
            await _respond_cv2(
                ctx,
                cv2_error(
                    "Nothing to convert", "This message has no embed to convert."
                ),
            )
            return

        container = embeds_to_container(message.embeds)
        if not container.components:
            await _respond_cv2(
                ctx,
                cv2_error(
                    "Nothing to convert",
                    "This message's embed has no content I can turn into components.",
                ),
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
                        cv2_error("I don't have permission to edit that message.")
                    ],
                )
                mctx.stop_interacting()
                return
            except h.BadRequestError as e:
                await mctx.respond(
                    edit=True,
                    flags=h.MessageFlag.IS_COMPONENTS_V2,
                    components=[
                        cv2_error(
                            "Discord rejected the converted message",
                            f"```\n{e}\n```",
                        )
                    ],
                )
                mctx.stop_interacting()
                return

            link = message.make_link(ctx.guild_id)
            await mctx.respond(
                edit=True,
                flags=h.MessageFlag.IS_COMPONENTS_V2,
                components=[cv2_success(f"Converted: {link}")],
            )
            mctx.stop_interacting()

        async def on_cancel(mctx: lbc.MenuContext) -> None:
            nonlocal handled
            handled = True
            await mctx.respond(
                edit=True,
                flags=h.MessageFlag.IS_COMPONENTS_V2,
                components=[cv2_notice("Cancelled — the message was left unchanged.")],
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
                        cv2_notice(
                            "⏱️ Timed out — nothing changed. Run the command again."
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
loader.command(EditPost, guilds=_post_guilds)
loader.command(CopyPost, guilds=_post_guilds)
loader.command(ConvertToComponents, guilds=_post_guilds)
