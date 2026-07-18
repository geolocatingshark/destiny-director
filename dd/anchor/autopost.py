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

import hikari as h
import lightbulb as lb

from dd.hmessage import HMessage

from ..common.bot import CachedFetchBot
from ..common.components import cv2_error, cv2_notice, cv2_success
from . import utils

logger = logging.getLogger(__name__)


async def discord_announcer(
    bot: CachedFetchBot,
    channel_id: int,
    construct_message_coro: t.Callable[..., t.Awaitable[HMessage]],
    check_enabled: bool = False,
    enabled_check_coro: t.Callable[[], t.Awaitable[bool | None]] | None = None,
    publish_message: bool = True,
    cv2: bool = False,
):
    """Build a message and send (optionally crossposting) it to ``channel_id``.

    The shared announce path for the automatic followable producers (Lost Sector, Iron
    Banner, …) and the ``send`` subcommand of :func:`make_autopost_control_commands`.
    ``check_enabled`` gates on ``enabled_check_coro`` (the producer's autopost getter);
    message construction retries with capped exponential backoff so a transient error
    (manifest/Discord blip) doesn't drop the post.
    """
    # ``cv2`` is accepted for parity with ``make_autopost_control_commands`` (its
    # ``send`` passes it through); this announcer creates a fresh message rather than
    # editing a placeholder, so the sent format is whatever ``construct_message_coro``
    # returns — no flag-toggle constraint to honour here.
    hmessage: HMessage | None = None
    # ``retries`` lives outside the loop so the backoff actually grows on a sustained
    # failure (a reset-each-iteration counter stays pinned at 2s).
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

    logger.info("Announcing post to channel %s", channel_id)
    await utils.send_message(
        bot,
        hmessage,
        channel_id=channel_id,
        crosspost=publish_message,
        deduplicate=True,
    )
    logger.info("Announced post to channel %s", channel_id)


def make_autopost_control_commands(
    autopost_name: str,
    enabled_getter: t.Callable[[], t.Awaitable[bool]],
    enabled_setter: t.Callable[..., t.Awaitable[t.Any]],
    channel_id: int,
    message_constructor_coro: t.Callable[..., t.Awaitable[HMessage]],
    message_announcer_coro: t.Callable[..., t.Awaitable[t.Any]] | None = None,
    cv2: bool = False,
) -> lb.Group:
    parent_group = lb.Group(autopost_name, "Commands for Kyber")

    @parent_group.register
    class AutopostControl(
        lb.SlashCommand,
        name="auto",
        description="Enable or disable automated announcements",
    ):
        option = lb.string(
            "option",
            "Enable or disable",
            choices=[lb.Choice("Enable", "Enable"), lb.Choice("Disable", "Disable")],
        )

        @lb.invoke
        async def invoke(self, ctx: lb.Context):
            enable = self.option.lower() == "enable"
            enabled = await enabled_getter()
            state = "enabled" if enable else "disabled"
            name = autopost_name.capitalize()
            if enable == enabled:
                await ctx.respond(
                    components=[
                        cv2_notice(f"{name} announcements are already {state}.")
                    ],
                    flags=h.MessageFlag.IS_COMPONENTS_V2,
                )
            else:
                await enabled_setter(enabled=enable)
                await ctx.respond(
                    components=[cv2_success(f"{name} announcements now {state}.")],
                    flags=h.MessageFlag.IS_COMPONENTS_V2,
                )

    @parent_group.register
    class ManualAnnounce(
        lb.SlashCommand,
        name="send",
        description="Trigger a discord announcement manually",
    ):
        publish = lb.boolean("publish", "Publish the announcement", default=True)

        @lb.invoke
        async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
            initial = await ctx.respond(
                components=[cv2_notice("Announcing…")],
                flags=h.MessageFlag.IS_COMPONENTS_V2,
            )
            if message_announcer_coro is None:
                await ctx.edit_response(
                    initial, components=[cv2_error("No announcer is configured")]
                )
                return
            try:
                await message_announcer_coro(
                    bot=bot,
                    channel_id=channel_id,
                    check_enabled=False,
                    construct_message_coro=message_constructor_coro,
                    publish_message=self.publish,
                    cv2=cv2,
                )
            except Exception as e:
                logger.exception(e)
                await ctx.edit_response(
                    initial, components=[cv2_error("Announcement failed", str(e))]
                )
            else:
                await ctx.edit_response(initial, components=[cv2_success("Announced")])

    @parent_group.register
    class Show(
        lb.SlashCommand,
        name="show",
        description="Check what the post will look like",
    ):
        @lb.invoke
        async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
            # Match the placeholder's type to the final message (CV2 vs embed) so the
            # in-place edit never toggles IS_COMPONENTS_V2 — Discord forbids that, but
            # editing a CV2 message's components (no flags arg) preserves the flag.
            if cv2:
                initial = await ctx.respond(
                    flags=h.MessageFlag.IS_COMPONENTS_V2,
                    components=[cv2_notice("Gathering data…")],
                )
            else:
                # Not-yet-migrated (embed) post type: the placeholder is edited into
                # the embed preview below, so it must stay non-CV2 — Discord forbids
                # toggling IS_COMPONENTS_V2 on an edit. Becomes CV2 once the post does.
                initial = await ctx.respond("Gathering data...")
            try:
                message: HMessage = await message_constructor_coro(bot=bot)
            except Exception as e:
                logger.exception(e)
                if cv2:
                    await ctx.edit_response(
                        initial,
                        components=[cv2_error("Something went wrong", str(e))],
                    )
                else:
                    await ctx.edit_response(initial, "An error occurred!\n" + str(e))
                return
            if cv2:
                await ctx.edit_response(initial, components=message.components)
            else:
                await ctx.edit_response(initial, **message.to_message_kwargs())

    return parent_group
