# Copyright © 2019-present gsfernandes81

# This file is part of "destiny-director".

# destiny-director is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later version.

# "destiny-director" is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License along with
# destiny-director. If not, see <https://www.gnu.org/licenses/>.

import logging

import hikari as h
import lightbulb as lb

from ..common import cfg
from .embeds import build_embed_with_user


@lb.command(
    "post",
    "Post management commands",
    hidden=True,
    guilds=[cfg.kyber_discord_server_id, cfg.control_discord_server_id],
)
@lb.implements(lb.SlashCommandGroup)
async def post_group(ctx: lb.Context):
    pass


@post_group.child
@lb.command("create", "Create a new post", hidden=True, ephemeral=True)
@lb.implements(lb.SlashSubCommand)
async def create_post(ctx: lb.Context):
    if ctx.author.id not in cfg.admins:
        return await ctx.respond("You are not an admin")

    embed = await build_embed_with_user(ctx, done_button_text="Post")
    try:
        await ctx.get_channel().send(embed)
    except h.ForbiddenError as e:
        await ctx.edit_last_response(
            "**orbiddenError**: It looks like I do not have permission to send messages here"
        )
        logging.exception(e)
    except h.BadRequestError as e:
        await ctx.edit_last_response(
            "**BadRequestError**: It looks like the embed is either too large, has "
            + "too many attachments, has attachments that are too large, or has "
            + "exceeded some other limit. See description from documentation below:"
            + "\n"
            + "```\n"
            + "This may be raised in several discrete situations, such as messages "
            + "being empty with no attachments or embeds; messages with more than "
            + "2000 characters in them, embeds that exceed one of the many embed "
            + "limits; too many attachments; attachments that are too large; invalid "
            + "image URLs in embeds; reply not found or not in the same channel; too "
            + "many components.\n"
            + "```\n"
        )
        logging.exception(e)


@lb.command(
    "edit",
    "Edit a post",
    hidden=True,
    ephemeral=True,
    guilds=[cfg.kyber_discord_server_id, cfg.control_discord_server_id],
)
@lb.implements(lb.MessageCommand)
async def edit_post(ctx: lb.MessageContext):
    if ctx.author.id not in cfg.admins:
        return await ctx.respond("You are not an admin")

    message: h.Message = ctx.options.target

    if not message.author.id == ctx.bot.get_me().id:
        return await ctx.respond("Can only edit messages posted by this bot")

    if not (message.embeds and len(message.embeds) == 1):
        return await ctx.respond("Can only edit messages with 1 embed")

    embed = await build_embed_with_user(
        ctx, done_button_text="Edit", existing_embed=message.embeds[0]
    )

    await message.edit(embed=embed)


@lb.command(
    "copy",
    "Copy, edit and then send a post",
    hidden=True,
    ephemeral=True,
    guilds=[cfg.kyber_discord_server_id, cfg.control_discord_server_id],
)
@lb.implements(lb.MessageCommand)
async def copy_post(ctx: lb.MessageContext):
    if ctx.author.id not in cfg.admins:
        return await ctx.respond("You are not an admin")

    message: h.Message = ctx.options.target

    if not (message.embeds and len(message.embeds) == 1):
        return await ctx.respond("Can only edit messages with 1 embed")

    embed = await build_embed_with_user(
        ctx, done_button_text="Send", existing_embed=message.embeds[0]
    )

    await ctx.get_channel().send(embed=embed)


def register(bot: lb.BotApp):
    bot.command(post_group)
    bot.command(edit_post)
    bot.command(copy_post)
