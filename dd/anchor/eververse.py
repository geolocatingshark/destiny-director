import typing as t
from collections import defaultdict

import aiocron
import hikari as h
import lightbulb as lb
from hmessage import HMessage

from ..common import cfg, schemas
from ..common.utils import fetch_emoji_dict
from . import bungie_api as api
from . import xur
from .autopost import make_autopost_control_commands
from .embeds import substitute_user_side_emoji


async def eververse_message_constructor(bot: lb.BotApp) -> HMessage:
    classes = ["Hunter", "Titan", "Warlock"]
    sale_items_dict: t.Dict[str, t.Set[api.DestinyItem]] = defaultdict(set)
    for class_ in classes:
        eververse_data = await xur.fetch_vendor_data(
            bot.d.webserver_runner, vendor_hashes=3361454721, character_class=class_
        )
        for sale_item in eververse_data.sale_items:
            sale_items_dict[class_].add(sale_item)

    hunter_sale_items = sale_items_dict["Hunter"] - (
        sale_items_dict["Titan"] | sale_items_dict["Warlock"]
    )
    titan_sale_items = sale_items_dict["Titan"] - (
        sale_items_dict["Hunter"] | sale_items_dict["Warlock"]
    )
    warlock_sale_items = sale_items_dict["Warlock"] - (
        sale_items_dict["Hunter"] | sale_items_dict["Titan"]
    )
    common_sale_items = (
        sale_items_dict["Hunter"]
        & sale_items_dict["Titan"]
        & sale_items_dict["Warlock"]
    )

    for class_, class_specific_sale_items in zip(
        classes, [hunter_sale_items, titan_sale_items, warlock_sale_items]
    ):
        for sale_item in class_specific_sale_items:
            sale_item.class_ = class_

    eververse_data.sale_items = (
        hunter_sale_items | titan_sale_items | warlock_sale_items | common_sale_items
    )

    return await format_eververse_vendor(eververse_data, bot)


async def format_eververse_vendor(vendor: api.DestinyVendor, bot: lb.BotApp):
    # Sort items out into categories based on their item_type_friendly_name
    # then sort packages into Hunter, Titan and Warlock based on the source
    # data from the calling function

    emoji_dict = await fetch_emoji_dict(bot)

    hunter_specific_items = []
    titan_specific_items = []
    warlock_specific_items = []
    remaining_items = defaultdict(list)

    for sale_item in vendor.sale_items:
        if "bright dust" not in str(sale_item.costs).lower():
            continue

        # Manually exclude DokiDoki Bundles from eververse returned from the API
        # The below two lines should be removed at a later date when this is not a
        # problem
        if sale_item.name.startswith("Doki Doki Destiny "):
            continue

        if sale_item.class_ == "Hunter":
            hunter_specific_items.append(sale_item)
        elif sale_item.class_ == "Titan":
            titan_specific_items.append(sale_item)
        elif sale_item.class_ == "Warlock":
            warlock_specific_items.append(sale_item)
        else:
            remaining_items[sale_item.item_type_friendly_name].append(sale_item)

    description = "# [This Week 𝘢𝘵 Eververse](https://kyber3000.com/Eververse)\n\n"
    description += "**__BRIGHT DUST OFFERINGS__** :bright_dust:\n\n"
    description += "⇣ All items below cost Bright Dust ⇣\n\n"

    for class_, class_specific_sale_items in zip(
        ["Hunter", "Titan", "Warlock"],
        [
            hunter_specific_items,
            titan_specific_items,
            warlock_specific_items,
        ],
    ):
        if not class_specific_sale_items:
            continue

        description += f"**{class_} Specific Items**\n"
        for item in class_specific_sale_items:
            description += f"• {item.name} ({item.costs['Bright Dust']})\n"
        description += "\n"

    for item_type, items in remaining_items.items():
        description += f"**{item_type}s**\n"
        for item in items:
            item: api.DestinyItem
            item.lightgg_url
            description += (
                f"• [{item.name}]({item.lightgg_url}) "
                f"({item.costs['Bright Dust']})\n"
            )
        description += "\n"

    description = await substitute_user_side_emoji(emoji_dict, description)

    embed = h.Embed(
        description=description,
        color=h.Color(cfg.embed_default_color),
        url="https://kyberscorner.com",
    )

    message = HMessage(embeds=[embed])
    return message


async def on_start_schedule_autoposts(event: lb.LightbulbStartedEvent):
    # Run every day at 17:00 UTC
    @aiocron.crontab("0 17 * * TUE", start=True)
    # Use below crontab for testing to post every minute
    # @aiocron.crontab("* * * * *", start=True)
    async def autopost_eververse():
        await xur.api_to_discord_announcer(
            event.app,
            channel_id=cfg.followables["eververse"],
            check_enabled=True,
            enabled_check_coro=schemas.AutoPostSettings.get_eververse_enabled,
            construct_message_coro=eververse_message_constructor,
        )


def register(bot: lb.BotApp) -> None:
    bot.listen(lb.LightbulbStartedEvent)(on_start_schedule_autoposts)
    bot.command(
        make_autopost_control_commands(
            autopost_name="eververse",
            enabled_getter=schemas.AutoPostSettings.get_eververse_enabled,
            enabled_setter=schemas.AutoPostSettings.set_eververse,
            channel_id=cfg.followables["eververse"],
            message_constructor_coro=eververse_message_constructor,
            message_announcer_coro=xur.api_to_discord_announcer,
        )
    )
