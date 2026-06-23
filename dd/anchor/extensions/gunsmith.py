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


async def gunsmith_message_constructor(bot: CachedFetchBot) -> HMessage:
    gunsmith_data = await xur.fetch_vendor_data(
        api.get_webserver_runner(), vendor_hashes=[672118013]
    )
    return await format_gunsmith_vendor(gunsmith_data, bot)


async def format_gunsmith_vendor(
    vendor: api.DestinyVendor, bot: CachedFetchBot
) -> HMessage:
    emoji_dict = await fetch_emoji_dict(bot)

    # Sale items with a cost are the featured items for banshee / gunsmith
    # on that day. We want featured items that are also weapons
    featured_weapons = [
        item for item in vendor.sale_items if item.costs and item.is_weapon
    ]

    # To format as per Kyber's request
    description = "# [GUNSMITH'S FEATURED WEAPONS](https://kyber3000.com)\n\n"
    description += xur.legendary_weapons_fragment(
        featured_weapons, emoji_include_list=emoji_dict.keys(), include_title=""
    )
    description = await substitute_user_side_emoji(emoji_dict, description)

    embed = h.Embed(
        description=description,
        color=h.Color(cfg.embed_default_color),
        url="https://kyberscorner.com",
    )

    embed.set_image(cfg.gunsmith_image_url)

    message = HMessage(embeds=[embed])
    return message


@loader.listener(h.StartedEvent)
async def on_start_schedule_autoposts(
    event: h.StartedEvent, bot: CachedFetchBot = lb.di.INJECTED
):
    # Run every Tuesday at 17:01 UTC
    # TO BE RECHECKED BASED ON KYBERS REPLY
    @aiocron.crontab("1 17 * * TUE", start=True)
    # Use below crontab for testing to post every minute
    # @aiocron.crontab("* * * * *", start=True)
    async def autopost_gunsmith():
        await xur.api_to_discord_announcer(
            bot,
            channel_id=cfg.followables["gunsmith"],
            check_enabled=True,
            enabled_check_coro=schemas.AutoPostSettings.get_gunsmith_enabled,
            construct_message_coro=gunsmith_message_constructor,
        )


async def _get_gunsmith_enabled() -> bool:
    return bool(await schemas.AutoPostSettings.get_gunsmith_enabled())


_gunsmith_autopost_group = make_autopost_control_commands(
    autopost_name="gunsmith",
    enabled_getter=_get_gunsmith_enabled,
    enabled_setter=schemas.AutoPostSettings.set_gunsmith,
    channel_id=cfg.followables["gunsmith"],
    message_constructor_coro=gunsmith_message_constructor,
    message_announcer_coro=xur.api_to_discord_announcer,
)

loader.command(_gunsmith_autopost_group)
