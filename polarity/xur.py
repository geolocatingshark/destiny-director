# Copyright © 2019-present gsfernandes81

# This file is part of "mortal-polarity".

# mortal-polarity is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later version.

# "mortal-polarity" is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License along with
# mortal-polarity. If not, see <https://www.gnu.org/licenses/>.

import asyncio as aio
import datetime as dt
import logging
import typing as t

import aiocron
import aiohttp
import hikari as h
import lightbulb as lb
from hmessage import HMessage
from sector_accounting import xur as xur_support_data

from . import bungie_api as api
from . import cfg, schemas, utils
from .autopost import make_autopost_control_commands
from .embeds import substitute_user_side_emoji

logger = logging.getLogger(__name__)


def xur_departure_string(post_date_time: dt.datetime | None = None) -> str:
    # Find the closest Tuesday in the future and set the time
    # to 1700 UTC on that day
    if post_date_time is None:
        post_date_time = dt.datetime.now(tz=dt.timezone.utc)

    # Find the next Tuesday
    days_ahead = 1 - post_date_time.weekday()
    days = days_ahead % 7
    post_date_time = post_date_time + dt.timedelta(days=days)

    # Set the time to 1700 UTC
    post_date_time = post_date_time.replace(hour=17, minute=0, second=0, microsecond=0)

    # Convert to unix time
    xur_unix_departure_time = int(post_date_time.timestamp())

    return f":time:  Xûr departs <t:{xur_unix_departure_time}:R>\n"


def xur_location_fragment(
    xur_location: str, xur_locations: xur_support_data.XurLocations
) -> str:
    xur_location = xur_locations[xur_location]
    return f"## **__Location__**\n:location: {str(xur_location)}\n"


def armor_stat_line_format(armor: api.DestinyArmor, simple_mode: bool = False) -> str:
    if simple_mode:
        return f"- Stat: {armor.stat_total}"
    stats = armor.stats
    stat_line = "- "
    for stat_name, stat_value in stats.items():
        stat_line += f":rotate: {stat_value} "

    stat_line += f"\n- Total: {armor.stat_total}"
    return stat_line


def exotic_armor_fragment(exotic_armor_pieces: t.List[api.DestinyArmor]) -> str:
    subfragments: t.List[str] = []
    for armor_piece in exotic_armor_pieces:
        subfragments.append(
            f":{armor_piece.class_.lower().capitalize()}:  [{armor_piece.name} "
            + f"({armor_piece.bucket})]({armor_piece.lightgg_url})\n"
            + armor_stat_line_format(armor_piece)
        )
    return "## **__Exotic Armor__**\n" + "\n".join(subfragments) + "\n"


def weapon_line_format(
    weapon: api.DestinyWeapon,
    include_weapon_type: bool,
    include_perks: t.List[int],
    include_lightgg_link: bool,
    emoji_include_list: t.List[str] = ["weapon"],
    default_emoji: str = "weapon",
) -> str:
    weapon_line = weapon.name

    if emoji_include_list:
        if weapon.expected_emoji_name in emoji_include_list:
            weapon_line = f":{weapon.expected_emoji_name}: {weapon_line}"
        else:
            weapon_line = f":{default_emoji}: {weapon_line}"

    if include_weapon_type:
        weapon_line += f" ({weapon.item_type_friendly_name})"
    if include_perks:
        perks = []
        for perk_index in include_perks:
            if perk_index >= len(weapon.perks):
                continue

            perk_options = weapon.perks[perk_index]
            if isinstance(perk_options, tuple):
                perks.append(" / ".join(perk_options))
            else:
                perks.append(perk_options)

        perks = ": " + ", ".join(perks)
        weapon_line += perks

    if include_lightgg_link:
        weapon_line = f"[{weapon_line}]({weapon.lightgg_url})"

    return weapon_line


def exotic_weapons_fragment(
    exotic_weapons: t.List[api.DestinyWeapon], emoji_include_list: t.List[str]
) -> str:
    exotic_weapons_fragment_ = "## **__Exotic Weapons__**\n\n"
    for exotic_weapon in exotic_weapons:
        exotic_weapons_fragment_ += (
            weapon_line_format(
                exotic_weapon,
                include_weapon_type=False if exotic_weapon.name == "Hawkmoon" else True,
                include_perks=[1] if exotic_weapon.name == "Hawkmoon" else [],
                include_lightgg_link=True,
                emoji_include_list=emoji_include_list,
            )
            + "\n"
        )
    return exotic_weapons_fragment_


def legendary_armor_fragement(
    legendary_armor_pieces: t.List[api.DestinyArmor],
    xur_armor_sets_data: xur_support_data.XurArmorSets,
) -> str:
    armor_sets = set()
    for armor_piece in legendary_armor_pieces:
        armor_set_name = armor_piece.armor_set_name
        if armor_set_name:
            armor_sets.add(armor_set_name)

    subfragments = []
    subfragments.append("## **__Legendary Armor__**")
    subfragments.append("")

    for armor_set_name in armor_sets:
        armor_set = xur_armor_sets_data[armor_set_name]
        subfragments.append(f":armor: {armor_set}")

    subfragments.append("")

    return "\n".join(subfragments)


def legendary_weapons_fragment(
    legendary_weapons: t.List[api.DestinyArmor], emoji_include_list: t.List[str]
) -> str:
    subfragments = []
    subfragments.append("## **__Legendary Weapons__**")
    subfragments.append("")

    for weapon in legendary_weapons:
        subfragments.append(
            weapon_line_format(
                weapon,
                include_weapon_type=True,
                include_perks=[-4, -3],
                include_lightgg_link=True,
                emoji_include_list=emoji_include_list,
            )
        )

    return "\n".join(subfragments)


XUR_FOOTER = """\n\n[**View More**](https://kyber3000.com/D2-Xur) ↗ 

Have a great weekend! :gscheer:"""


async def format_xur_vendor(
    vendor: api.DestinyVendor,
    bot: lb.BotApp = {},
) -> HMessage:
    xur_locations = xur_support_data.XurLocations.from_gspread_url(
        cfg.sheets_ls_url, cfg.gsheets_credentials
    )
    xur_armor_sets = xur_support_data.XurArmorSets.from_gspread_url(
        cfg.sheets_ls_url, cfg.gsheets_credentials
    )

    guild = bot.cache.get_guild(
        cfg.kyber_discord_server_id
    ) or await bot.rest.fetch_guild(cfg.kyber_discord_server_id)

    emoji_dict = {emoji.name: emoji for emoji in await guild.fetch_emojis()}

    description = "# [XÛR'S LOOT](https://kyber3000.com/D2-Xur)\n\n"
    description += xur_departure_string()
    description += xur_location_fragment(vendor.location, xur_locations)
    description += exotic_armor_fragment(
        [item for item in vendor.sale_items if item.is_exotic and item.is_armor]
    )
    description += exotic_weapons_fragment(
        [item for item in vendor.sale_items if item.is_exotic and item.is_weapon],
        emoji_include_list=emoji_dict.keys(),
    )
    description += legendary_armor_fragement(
        [item for item in vendor.sale_items if item.is_armor and item.is_legendary],
        xur_armor_sets,
    )
    description += legendary_weapons_fragment(
        [item for item in vendor.sale_items if item.is_weapon and item.is_legendary],
        emoji_include_list=emoji_dict.keys(),
    )

    description += XUR_FOOTER
    description = await substitute_user_side_emoji(emoji_dict, description)
    message = HMessage(
        embeds=[
            h.Embed(
                title="WEEK 20",
                description=description,
                color=h.Color(cfg.embed_default_color),
                url="https://kyberscorner.com",
            )
        ]
    )
    return message


async def xur_message_constructor(bot: lb.BotApp) -> HMessage:
    access_token = await api.refresh_api_tokens(bot.d.webserver_runner)

    async with aiohttp.ClientSession() as session:
        destiny_membership = await api.DestinyMembership.from_api(session, access_token)
        character_id = await destiny_membership.get_character_id(session, access_token)

    xur: api.DestinyVendor = await api.DestinyVendor.request_from_api(
        destiny_membership=destiny_membership,
        character_id=character_id,
        access_token=access_token,
        manifest_table=await api._build_manifest_dict(
            await api._get_latest_manifest(schemas.BungieCredentials.api_key)
        ),
        vendor_hash=api.XUR_VENDOR_HASH,
    )

    return await format_xur_vendor(xur, bot=bot)


async def xur_discord_announcer(
    bot: lb.BotApp,
    channel_id: int,
    construct_message_coro: t.Coroutine[t.Any, t.Any, HMessage] = None,
    check_enabled: bool = False,
    enabled_check_coro: t.Coroutine[t.Any, t.Any, bool] = None,
):
    hmessage = HMessage(
        embeds=[
            h.Embed(
                description="Waiting for Xur data from the API...",
                color=cfg.embed_default_color,
            )
        ]
    )
    msg = await utils.send_message(
        bot,
        hmessage,
        channel_id=channel_id,
        crosspost=False,
        deduplicate=True,
    )

    while True:
        retries = 0
        try:
            if check_enabled and not await enabled_check_coro():
                return

            await api.check_bungie_api_online(raise_exception=True)

            hmessage: HMessage = await construct_message_coro(bot)
        except api.APIOfflineException as e:
            logger.exception(e)
            retries += 1
            await aio.sleep(2 ** min(retries, 8))
        except Exception as e:
            logger.exception(e)
            retries += 1
            await aio.sleep(2 ** min(retries, 8))
        else:
            break

    while True:
        retries = 0
        try:
            if check_enabled and not await enabled_check_coro():
                return
            await msg.edit(**hmessage.to_message_kwargs())
        except Exception as e:
            logger.exception(e)
            retries += 1
            await aio.sleep(min(2**retries, 300))
        else:
            break

    await utils.crosspost_message_with_retries(bot, channel_id, msg.id)


async def on_start_schedule_autoposts(event: lb.LightbulbStartedEvent):
    # Run every day at 17:00 UTC
    @aiocron.crontab("0 17 * * FRI", start=True)
    # Use below crontab for testing to post every minute
    # @aiocron.crontab("* * * * *", start=True)
    async def autopost_xur():
        await xur_discord_announcer(
            event.app,
            channel_id=cfg.followables["xur"],
            check_enabled=True,
            enabled_check_coro=schemas.AutoPostSettings.get_lost_sector_enabled,
            construct_message_coro=xur_message_constructor,
        )


def register(bot: lb.BotApp) -> None:
    bot.listen(lb.LightbulbStartedEvent)(on_start_schedule_autoposts)
    bot.command(
        make_autopost_control_commands(
            autopost_name="xur",
            enabled_getter=schemas.AutoPostSettings.get_xur_enabled,
            enabled_setter=schemas.AutoPostSettings.set_xur,
            channel_id=cfg.followables["xur"],
            message_constructor_coro=xur_message_constructor,
            message_announcer_coro=xur_discord_announcer,
        )
    )
