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
import miru as m

from ..common import cfg
from ..common.utils import (
    construct_emoji_substituter,
    follow_link_single_step,
    re_user_side_emoji,
)


async def substitute_user_side_emoji(
    bot_or_emoji_dict: h.GatewayBot | dict[str, h.Emoji], text: str
) -> str:
    """Substitutes user-side emoji with their respective mentions"""

    emoji_dict: dict[str, h.Emoji]
    if isinstance(bot_or_emoji_dict, h.GatewayBot):
        guild = bot_or_emoji_dict.cache.get_guild(
            cfg.kyber_discord_server_id
        ) or await bot_or_emoji_dict.rest.fetch_guild(cfg.kyber_discord_server_id)

        emoji_dict = {emoji.name: emoji for emoji in await guild.fetch_emojis()}
    else:
        emoji_dict = bot_or_emoji_dict

    # Substitutes user-side emoji with their respective mentions
    return re_user_side_emoji.sub(construct_emoji_substituter(emoji_dict), text)


class InteractiveBuilderView(m.View):
    @staticmethod
    async def ask_user_for_properties(
        ctx: m.ViewContext,
        property_names: str | list[str],
        old_values: str | list[str],
        required: bool | list[bool] = True,
        multi_line: bool = False,
    ) -> str | list[str] | None:
        """Asks the user for a property of the embed using a modal

        Returns the new value of the property if the user responds"""

        names: list[str] = (
            property_names if isinstance(property_names, list) else [property_names]
        )
        values_in: list[str] = (
            old_values if isinstance(old_values, list) else [old_values]
        )
        required_list: list[bool] = (
            required if isinstance(required, list) else [required] * len(names)
        )

        if not len(names) == len(values_in):
            raise ValueError("property_names and old_values must be the same length")

        modal = m.Modal(title=f"Edit {', '.join(names)}")
        custom_ids = [
            f"embed_{property_name.lower().replace(' ', '_')}"
            for property_name in names
        ]

        style = h.TextInputStyle.PARAGRAPH if multi_line else h.TextInputStyle.SHORT

        for custom_id, old_value, property_name, required_ in zip(
            custom_ids, values_in, names, required_list, strict=True
        ):
            modal.add_item(
                m.TextInput(
                    label=property_name,
                    value=old_value,
                    style=style,
                    required=required_,
                    custom_id=custom_id,
                )
            )

        await ctx.respond_with_modal(modal)
        await modal.wait()

        if not modal.last_context:
            return None

        await modal.last_context.defer()

        values = [
            str(modal.last_context.get_value_by_id(custom_id) or "")
            for custom_id in custom_ids
        ]

        return values[0] if len(values) == 1 else values


class EmbedBuilderView(InteractiveBuilderView):
    """A view for building embeds as per user input"""

    def __init__(self, done_button_text: str = "Done"):
        super().__init__(timeout=840)
        self.embed: h.Embed | None = None
        t.cast(m.Button, t.cast(object, self.done)).label = done_button_text

    # @m.button(style=h.ButtonStyle.PRIMARY, label="Add Field")
    # async def add_field(self, button: m.Button, ctx: m.ViewContext):
    #     """Adds a field to the embed"""
    #     pass

    # @m.button(style=h.ButtonStyle.DANGER, label="Remove Field")
    # async def remove_field(self, button: m.Button, ctx: m.ViewContext):
    #     """Removes a field from the embed"""
    #     pass

    @m.button(style=h.ButtonStyle.SECONDARY, label="Edit Title")
    async def edit_title(self, button: m.Button, ctx: m.ViewContext):
        """Edits the embed's title"""
        embed = ctx.message.embeds[0]
        embed.title = t.cast(
            "str | None",
            await self.ask_user_for_properties(
                ctx, "Title", embed.title or "", required=False
            ),
        )
        await ctx.edit_response(embed=embed)

    @m.button(style=h.ButtonStyle.SECONDARY, label="Edit Text")
    async def edit_description(self, button: m.Button, ctx: m.ViewContext):
        """Edits the embed's description"""
        embed: h.Embed = ctx.message.embeds[0]
        description = t.cast(
            "str | None",
            await self.ask_user_for_properties(
                ctx, "Body", embed.description or "", multi_line=True, required=False
            ),
        )
        bot = t.cast(h.GatewayBot, ctx.bot)

        embed.description = await substitute_user_side_emoji(bot, description or "")
        await ctx.edit_response(embed=embed)

    @m.button(style=h.ButtonStyle.SECONDARY, label="Edit Color")
    async def edit_color(self, button: m.Button, ctx: m.ViewContext):
        """Edits the embed's color"""
        embed = ctx.message.embeds[0]
        color = t.cast(
            "str | None",
            await self.ask_user_for_properties(
                ctx,
                "Color",
                str(embed.color or cfg.embed_default_color),
                required=False,
            ),
        )
        if not color:
            return
        try:
            embed.color = h.Color.of(color)
        except ValueError as e:
            logging.error(f"Invalid color: {color}")
            logging.exception(e)
        else:
            await ctx.edit_response(embed=embed)

    @m.button(style=h.ButtonStyle.SECONDARY, label="Edit Author")
    async def edit_author(self, button: m.Button, ctx: m.ViewContext):
        """Edits the embed's author text"""
        embed: h.Embed = ctx.message.embeds[0]

        name = (embed.author.name or "") if embed.author else ""
        icon = (
            (embed.author.icon.url if embed.author.icon else "") if embed.author else ""
        )
        url = (embed.author.url or "") if embed.author else ""

        result = await self.ask_user_for_properties(
            ctx,
            ["Author", "Icon URL", "Author URL"],
            [name, icon, url],
            required=False,
        )
        # None → the modal was dismissed; leave the author untouched.
        if result is None:
            return
        name, icon, url = t.cast("list[str]", result)

        embed.set_author(name=name or None, icon=icon or None, url=url or None)
        await ctx.edit_response(embed=embed)

    @m.button(style=h.ButtonStyle.SECONDARY, label="Edit Image")
    async def edit_image(self, button: m.Button, ctx: m.ViewContext):
        """Edits the embed's image"""

        embed = ctx.message.embeds[0]
        image_url = t.cast(
            "str | None",
            await self.ask_user_for_properties(
                ctx,
                "Image URL",
                embed.image.url if embed.image else "",
                required=False,
            ),
        )
        # None → the modal was cancelled (leave the image as-is); an empty string →
        # the user cleared the field, so remove the image.
        if image_url is None:
            return

        embed.set_image(await follow_link_single_step(image_url) if image_url else None)

        await ctx.edit_response(embed=embed)

    @m.button(style=h.ButtonStyle.SECONDARY, label="Edit Thumbnail")
    async def edit_thumbnail(self, button: m.Button, ctx: m.ViewContext):
        """Edits the embed's thumbnail"""

        embed = ctx.message.embeds[0]
        thumbnail_url = t.cast(
            "str | None",
            await self.ask_user_for_properties(
                ctx,
                "Thumbnail URL",
                embed.thumbnail.url if embed.thumbnail else "",
                required=False,
            ),
        )
        # None → the modal was cancelled (leave the thumbnail as-is); an empty string →
        # the user cleared the field, so remove the thumbnail.
        if thumbnail_url is None:
            return

        embed.set_thumbnail(
            await follow_link_single_step(thumbnail_url) if thumbnail_url else None
        )

        await ctx.edit_response(embed=embed)

    @m.button(style=h.ButtonStyle.SECONDARY, label="Edit Footer")
    async def edit_footer(self, button: m.Button, ctx: m.ViewContext):
        """Edits the embed's footer text"""
        embed: h.Embed = ctx.message.embeds[0]

        text = (embed.footer.text or "") if embed.footer else ""
        icon = (
            (embed.footer.icon.url if embed.footer.icon else "") if embed.footer else ""
        )

        result = await self.ask_user_for_properties(
            ctx,
            ["Footer", "Icon URL"],
            [text, icon],
            required=False,
        )
        # None → the modal was dismissed; leave the footer untouched.
        if result is None:
            return
        text, icon = t.cast("list[str]", result)

        embed.set_footer(text, icon=icon or None)
        await ctx.edit_response(embed=embed)

    @m.button(style=h.ButtonStyle.SUCCESS, label="Done")
    async def done(self, button: m.Button, ctx: m.ViewContext):
        """Finishes building the embed"""
        for item in self.children:
            item.disabled = True  # Disable all items attached to the view
        await ctx.edit_response(components=self)
        self.embed = ctx.message.embeds[0]
        self.stop()


async def build_embed_with_user(
    ctx: lb.Context,
    done_button_text: str = "Done",
    existing_embed: h.Embed | None = None,
) -> h.Embed | None:
    """Builds an embed as specified by the user

    Responds with a message with buttons allowing the user to specify
    embed properties. Returns the embed once the user is done."""
    embed = existing_embed or h.Embed(
        title="Embed Builder",
        description="Use the buttons below to build your embed!\n",
        color=cfg.embed_default_color,
    )

    view = EmbedBuilderView(done_button_text=done_button_text)
    # In lightbulb v3 ``ctx.respond`` returns a sentinel (-1) for the initial response
    # rather than a message id, so bind the miru view to the fetched initial response
    # message instead. Binding to the sentinel keys the view to a bogus message id and
    # every button click goes unrouted ("interaction failed").
    await ctx.respond(embed=embed, components=view, flags=h.MessageFlag.EPHEMERAL)
    message = await ctx.interaction.fetch_initial_response()
    await view.start(message=message)
    await view.wait()
    return view.embed
