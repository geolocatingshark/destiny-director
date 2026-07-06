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

import asyncio as aio
import logging
import typing as t

import aiocron
import hikari as h
import lightbulb as lb

from dd.hmessage import HMessage

from ...common import cfg, schemas
from ...common.bot import CachedFetchBot
from ...common.components import cv2_error, cv2_notice, cv2_success, respond_cv2
from ...common.lost_sector import format_post
from ...common.utils import guild_scope
from .. import utils
from ..autopost import make_autopost_control_commands

logger = logging.getLogger(__name__)

loader = lb.Loader()


async def discord_announcer(
    bot: CachedFetchBot,
    channel_id: int,
    construct_message_coro: t.Callable[..., t.Awaitable[HMessage]],
    check_enabled: bool = False,
    enabled_check_coro: t.Callable[[], t.Awaitable[bool | None]] | None = None,
    publish_message: bool = True,
    cv2: bool = False,
):
    # ``cv2`` is accepted for parity with ``make_autopost_control_commands`` (its
    # ``send`` passes it through); this announcer creates a fresh message rather than
    # editing a placeholder, so the sent format is whatever ``construct_message_coro``
    # returns — no flag-toggle constraint to honour here.
    hmessage: HMessage | None = None
    # ``retries`` lives outside the loop so the backoff actually grows on a
    # sustained failure (a reset-each-iteration counter stays pinned at 2s).
    retries = 0
    while True:
        try:
            if check_enabled and (
                enabled_check_coro is None or not await enabled_check_coro()
            ):
                return
            hmessage = await construct_message_coro(bot=bot)
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


class ControlLostSectorDetails(
    lb.SlashCommand,
    name="details",
    description="Control whether lost sector additional details and counts are enabled",
):
    option = lb.string(
        "option",
        "Enable or disable",
        choices=[lb.Choice("Enable", "Enable"), lb.Choice("Disable", "Disable")],
    )

    @lb.invoke
    async def invoke(self, ctx: lb.Context):
        """Enable or disable lost sector legendary weapon announcements"""
        desired_setting: bool = self.option.lower() == "enable"
        current_setting = (
            await schemas.AutoPostSettings.get_lost_sector_details_enabled()
        )

        if desired_setting == current_setting:
            await respond_cv2(
                ctx,
                cv2_notice(
                    f"Lost sector details are already "
                    f"{'enabled' if desired_setting else 'disabled'}."
                ),
            )
            return

        await schemas.AutoPostSettings.set_lost_sector_details(enabled=desired_setting)
        await respond_cv2(
            ctx,
            cv2_success(
                f"Lost sector details are now "
                f"{'enabled' if desired_setting else 'disabled'}."
            ),
        )


class LsUpdate(
    lb.MessageCommand,
    name="ls_update",
    description="Update a lost sector post",
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        """Correct a mistake in the lost sector announcement"""
        msg_to_update: h.Message = self.target

        if not await schemas.AutoPostSettings.get_lost_sector_enabled():
            await respond_cv2(
                ctx, cv2_error("Please enable autoposts before using this command.")
            )
            return

        logger.info("Correcting posts")

        initial = await ctx.respond(
            components=[cv2_notice("Updating post now…")],
            flags=h.MessageFlag.IS_COMPONENTS_V2,
            ephemeral=True,
        )

        message = await format_post(bot=bot)
        await msg_to_update.edit(**message.to_message_kwargs())
        await ctx.edit_response(initial, components=[cv2_success("Post updated")])


@loader.listener(h.StartedEvent)
async def on_start_schedule_autoposts(
    event: h.StartedEvent, bot: CachedFetchBot = lb.di.INJECTED
):
    # Run every day at 17:00 UTC
    @aiocron.crontab("0 17 * * *", start=True)
    # Use below crontab for testing to post every minute
    # @aiocron.crontab("* * * * *", start=True)
    async def autopost_ls():
        await discord_announcer(
            bot,
            channel_id=cfg.followables["lost_sector"],
            check_enabled=True,
            enabled_check_coro=schemas.AutoPostSettings.get_lost_sector_enabled,
            construct_message_coro=format_post,
        )


async def _get_lost_sector_enabled() -> bool:
    return bool(await schemas.AutoPostSettings.get_lost_sector_enabled())


_ls_autopost_group = make_autopost_control_commands(
    "ls",
    _get_lost_sector_enabled,
    schemas.AutoPostSettings.set_lost_sector,
    cfg.followables["lost_sector"],
    format_post,
    message_announcer_coro=discord_announcer,
    cv2=True,
)

_ls_autopost_group.register(ControlLostSectorDetails)

# Slash autopost group inherits the client default (control + test_env). The
# ls_update context-menu command additionally appears in the Kyber server.
loader.command(_ls_autopost_group)
loader.command(
    LsUpdate,
    guilds=guild_scope(
        *cfg.test_env,
        cfg.control_discord_server_id,
        cfg.kyber_discord_server_id,
    ),
)
