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

import logging
import typing as t

import hikari as h
import lightbulb as lb

from ...common import cfg, utils
from ...common.bot import CachedFetchBot
from ..embeds import build_embed_with_user

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
