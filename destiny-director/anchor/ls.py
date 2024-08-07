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

import asyncio as aio
import logging
import typing as t

import aiocron
import aiohttp
import hikari as h
import lightbulb as lb
from hmessage import HMessage as MessagePrototype

from ..common import cfg, schemas
from ..common.lost_sector import format_counts, get_emoji_dict
from ..common.utils import construct_emoji_substituter, re_user_side_emoji, space
from ..sector_accounting import sector_accounting
from . import utils
from .autopost import make_autopost_control_commands

logger = logging.getLogger(__name__)


async def format_sector(
    bot: lb.BotApp,
    secondary_image: h.Attachment | None = None,
    secondary_embed_title: str | None = "",
    secondary_embed_description: str | None = "",
) -> MessagePrototype:
    emoji_dict = await get_emoji_dict(bot)
    sector: sector_accounting.Sector = sector_accounting.Rotation.from_gspread_url(
        cfg.sheets_ls_url, cfg.gsheets_credentials, buffer=5
    )()

    # Follow the hyperlink to have the newest image embedded
    try:
        ls_gfx_url = await utils.follow_link_single_step(sector.shortlink_gfx)
    except aiohttp.InvalidURL:
        ls_gfx_url = None

    # Surges to emojis
    surges = []
    for surge in sector.surges:
        surges += [str(emoji_dict.get(surge) or emoji_dict.get(surge.lower()))]

    # Threat to emoji
    threat = emoji_dict.get(sector.threat) or emoji_dict.get(sector.threat.lower())

    overcharged_weapon_emoji = (
        "⚔️" if sector.overcharged_weapon.lower() in ["sword", "glaive"] else "🔫"
    )

    if "(" in sector.name or ")" in sector.name:
        sector_name = sector.name.split("(")[0].strip()
        sector_location = sector.name.split("(")[1].split(")")[0].strip()
    else:
        sector_name = sector.name
        sector_location = None

    # Legendary weapon rewards
    legendary_weapon_rewards = sector.legendary_rewards

    legendary_weapon_rewards = re_user_side_emoji.sub(
        construct_emoji_substituter(emoji_dict), legendary_weapon_rewards
    )

    embed = h.Embed(
        title="**Lost Sector Today**",
        description=(
            f"{emoji_dict['LS']}{space.three_per_em}{sector_name.strip()}\n"
            + (
                f"{emoji_dict['location']}{space.three_per_em}{sector_location.strip()}"
                if sector_location
                else ""
            )
        ),
        color=cfg.embed_default_color,
        url="https://lostsectortoday.com/",
    )

    embed.add_field(
        name="Rewards (If-Solo)",
        value=str(emoji_dict["exotic_engram"])
        + f"{space.three_per_em}Exotic {sector.reward}",
    )

    if await schemas.AutoPostSettings.get_lost_sector_legendary_weapons_enabled():
        embed.add_field(
            "Legendary Weapons (If-Solo)",
            legendary_weapon_rewards,
        )

        embed.add_field(
            "Drop Rate (with no champions left)",
            "Expert: 70%\n" "Master: 100% + double perks on weapons",
        )

    embed.add_field(
        name="Champs and Shields",
        value=format_counts(sector.legend_data, sector.master_data, emoji_dict),
    )
    embed.add_field(
        name="Elementals",
        value=f"Surge: {space.punctuation}{space.hair}{space.hair}"
        + " ".join(surges)
        + f"\nThreat: {threat}",
    )
    embed.add_field(
        name="Modifiers",
        value=str(emoji_dict["swords"])
        + f"{space.three_per_em}{sector.to_sector_v1().modifiers}"
        + f"\n{overcharged_weapon_emoji}{space.three_per_em}Overcharged {sector.overcharged_weapon}",
    )

    if ls_gfx_url:
        embed.set_image(ls_gfx_url)

    if secondary_image:
        embed2 = h.Embed(
            title=secondary_embed_title,
            description=secondary_embed_description,
            color=cfg.kyber_pink,
        )
        embed2.set_image(secondary_image)
        embeds = [embed, embed2]
    else:
        embeds = [embed]

    return MessagePrototype(embeds=embeds)


async def discord_announcer(
    bot: lb.BotApp,
    channel_id: int,
    construct_message_coro: t.Coroutine[t.Any, t.Any, MessagePrototype] = None,
    check_enabled: bool = False,
    enabled_check_coro: t.Coroutine[t.Any, t.Any, bool] = None,
    publish_message: bool = True,
):
    while True:
        retries = 0
        try:
            if check_enabled and not await enabled_check_coro():
                return
            hmessage = await construct_message_coro(bot)
        except Exception as e:
            logger.exception(e)
            retries += 1
            await aio.sleep(min(2**retries, 300))
        else:
            break

    logger.info("Announcing lost sector to discord")
    await utils.send_message(
        bot,
        hmessage,
        channel_id=channel_id,
        crosspost=publish_message,
        deduplicate=True,
    )
    logger.info("Announced lost sector to discord")


@lb.option(
    "option", "Enable or disable", str, choices=["Enable", "Disable"], required=True
)
@lb.command(
    "legendary_weapons",
    "Control lost sector legendary weapon announcements",
    auto_defer=True,
    pass_options=True,
)
@lb.implements(lb.SlashSubCommand)
async def control_legendary_weapons(ctx: lb.Context, option: str):
    """Enable or disable lost sector legendary weapon announcements"""

    desired_setting: bool = True if option.lower() == "enable" else False
    current_setting = (
        await schemas.AutoPostSettings.get_lost_sector_legendary_weapons_enabled()
    )

    if desired_setting == current_setting:
        return await ctx.respond(
            f"Lost sector legendary weapon announcements are already {'enabled' if desired_setting else 'disabled'}"
        )

    await schemas.AutoPostSettings.set_lost_sector_legendary_weapons(
        enabled=desired_setting
    )
    await ctx.respond(
        f"Lost sector legendary weapon announcements now {'enabled' if desired_setting else 'disabled'}"
    )


def sub_group(parent: lb.CommandLike, name: str, description: str):
    @lb.command(name, description)
    @lb.implements(lb.SlashSubGroup)
    def _():
        pass

    parent.child(_)

    return _


@lb.command("ls_update", "Update a lost sector post", ephemeral=True, auto_defer=True)
@lb.implements(lb.MessageCommand)
async def ls_update(ctx: lb.MessageContext):
    """Correct a mistake in the lost sector announcement"""

    if ctx.author.id not in cfg.admins:
        await ctx.respond("Only admins can use this command")
        return

    msg_to_update: h.Message = ctx.options.target

    async with schemas.db_session() as session:
        settings: schemas.AutoPostSettings = await session.get(
            schemas.AutoPostSettings, 0
        )
        if settings is None:
            await ctx.respond("Please enable autoposts before using this cmd")

        logger.info("Correcting posts")

        await ctx.edit_last_response("Updating post now")

        message = await format_sector(ctx.app)
        await msg_to_update.edit(**message.to_message_kwargs())
        await ctx.edit_last_response("Post updated")


async def on_start_schedule_autoposts(event: lb.LightbulbStartedEvent):
    # Run every day at 17:00 UTC
    @aiocron.crontab("0 17 * * *", start=True)
    # Use below crontab for testing to post every minute
    # @aiocron.crontab("* * * * *", start=True)
    async def autopost_ls():
        await discord_announcer(
            event.app,
            channel_id=cfg.followables["lost_sector"],
            check_enabled=True,
            enabled_check_coro=schemas.AutoPostSettings.get_lost_sector_enabled,
            construct_message_coro=format_sector,
        )


def register(bot: lb.BotApp) -> None:
    autopost_control_parent_group = make_autopost_control_commands(
        "ls",
        schemas.AutoPostSettings.get_lost_sector_enabled,
        schemas.AutoPostSettings.set_lost_sector,
        cfg.followables["lost_sector"],
        format_sector,
        message_announcer_coro=discord_announcer,
    )

    autopost_control_parent_group.child(control_legendary_weapons)

    bot.command(autopost_control_parent_group)

    bot.command(ls_update)
    bot.listen(lb.LightbulbStartedEvent)(on_start_schedule_autoposts)
