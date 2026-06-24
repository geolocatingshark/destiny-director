from collections import defaultdict

import aiocron
import hikari as h
import lightbulb as lb

from dd.hmessage import HMessage

from ...common import cfg, schemas
from ...common.bot import CachedFetchBot
from ...common.utils import fetch_emoji_dict
from ..autopost import make_autopost_control_commands
from ..embeds import substitute_user_side_emoji
from . import (
    bungie_api as api,
    xur,
)

loader = lb.Loader()


async def eververse_message_constructor(bot: CachedFetchBot) -> HMessage:
    classes = ["Hunter", "Titan", "Warlock"]
    sale_items_dict: dict[str, set[api.DestinyItem]] = defaultdict(set)
    eververse_data: api.DestinyVendor | None = None
    for class_ in classes:
        eververse_data = await xur.fetch_vendor_data(
            api.get_webserver_runner(),
            vendor_hashes=3361454721,
            character_class=class_,
        )
        for sale_item in eververse_data.sale_items:
            sale_items_dict[class_].add(sale_item)

    if eververse_data is None:
        raise RuntimeError("No eververse vendor data was fetched")

    # Eververse offers some items to only one class and some to every class.
    # Split the per-class sets into class-specific items (present in exactly one
    # class's set) and common items (present in all three) so each group can be
    # labelled with its class and rendered once.
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
        classes,
        [hunter_sale_items, titan_sale_items, warlock_sale_items],
        strict=True,
    ):
        for sale_item in class_specific_sale_items:
            sale_item.class_ = class_

    eververse_data.sale_items = list(
        hunter_sale_items | titan_sale_items | warlock_sale_items | common_sale_items
    )

    return await format_eververse_vendor(eververse_data, bot)


async def format_eververse_vendor(
    vendor: api.DestinyVendor, bot: CachedFetchBot
) -> HMessage:
    # Sort items out into categories based on their item_type_friendly_name
    # then sort packages into Hunter, Titan and Warlock based on the source
    # data from the calling function

    emoji_dict = await fetch_emoji_dict(bot)

    hunter_specific_items: list[api.DestinyItem] = []
    titan_specific_items: list[api.DestinyItem] = []
    warlock_specific_items: list[api.DestinyItem] = []
    remaining_items: defaultdict[str, list[api.DestinyItem]] = defaultdict(list)

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
        strict=True,
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
            description += (
                f"• [{item.name}]({item.lightgg_url}) ({item.costs['Bright Dust']})\n"
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


@loader.listener(h.StartedEvent)
async def on_start_schedule_autoposts(
    event: h.StartedEvent, bot: CachedFetchBot = lb.di.INJECTED
):
    # Run every Tuesday at 17:00 UTC
    @aiocron.crontab("0 17 * * TUE", start=True)
    # Use below crontab for testing to post every minute
    # @aiocron.crontab("* * * * *", start=True)
    async def autopost_eververse():
        await xur.api_to_discord_announcer(
            bot,
            channel_id=cfg.followables["eververse"],
            check_enabled=True,
            enabled_check_coro=schemas.AutoPostSettings.get_eververse_enabled,
            construct_message_coro=eververse_message_constructor,
        )


async def _get_eververse_enabled() -> bool:
    return bool(await schemas.AutoPostSettings.get_eververse_enabled())


_eververse_autopost_group = make_autopost_control_commands(
    autopost_name="eververse",
    enabled_getter=_get_eververse_enabled,
    enabled_setter=schemas.AutoPostSettings.set_eververse,
    channel_id=cfg.followables["eververse"],
    message_constructor_coro=eververse_message_constructor,
    message_announcer_coro=xur.api_to_discord_announcer,
)

loader.command(_eververse_autopost_group)
