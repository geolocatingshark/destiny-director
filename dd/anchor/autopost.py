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

import lightbulb as lb

from dd.hmessage import HMessage

from ..common import cfg
from ..common.bot import CachedFetchBot

logger = logging.getLogger(__name__)


def make_autopost_control_commands(
    autopost_name: str,
    enabled_getter: t.Callable[[], t.Awaitable[bool]],
    enabled_setter: t.Callable[..., t.Awaitable[t.Any]],
    channel_id: int,
    message_constructor_coro: t.Callable[..., t.Awaitable[HMessage]],
    message_announcer_coro: t.Callable[..., t.Awaitable[t.Any]] | None = None,
) -> lb.Group:
    parent_group = lb.Group(
        autopost_name if not cfg.test_env else "dev_" + autopost_name,
        "Commands for Kyber",
    )

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
            if enable == enabled:
                await ctx.respond(
                    "{} announcements are already {}".format(
                        autopost_name.capitalize(),
                        "enabled" if enable else "disabled",
                    )
                )
            else:
                await enabled_setter(enabled=enable)
                await ctx.respond(
                    "{} announcements now {}".format(
                        autopost_name.capitalize(),
                        "Enabled" if enable else "Disabled",
                    )
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
            initial = await ctx.respond("Announcing...")
            if message_announcer_coro is None:
                await ctx.edit_response(initial, "No announcer is configured")
                return
            try:
                await message_announcer_coro(
                    bot=bot,
                    channel_id=channel_id,
                    check_enabled=False,
                    construct_message_coro=message_constructor_coro,
                    publish_message=self.publish,
                )
            except Exception as e:
                logger.exception(e)
                await ctx.edit_response(initial, "An error occurred!\n" + str(e))
            else:
                await ctx.edit_response(initial, "Announced")

    @parent_group.register
    class Show(
        lb.SlashCommand,
        name="show",
        description="Check what the post will look like",
    ):
        @lb.invoke
        async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
            initial = await ctx.respond("Gathering data...")
            try:
                message: HMessage = await message_constructor_coro(bot=bot)
            except Exception as e:
                logger.exception(e)
                await ctx.edit_response(initial, "An error occurred!\n" + str(e))
            else:
                await ctx.edit_response(initial, **message.to_message_kwargs())

    return parent_group
