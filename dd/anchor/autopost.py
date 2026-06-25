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

from dd.hmessage import HMessage

from ..common.bot import CachedFetchBot
from ..common.components import build_container

logger = logging.getLogger(__name__)


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
                    cv2=cv2,
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
            # Match the placeholder's type to the final message (CV2 vs embed) so the
            # in-place edit never toggles IS_COMPONENTS_V2 — Discord forbids that, but
            # editing a CV2 message's components (no flags arg) preserves the flag.
            if cv2:
                initial = await ctx.respond(
                    flags=h.MessageFlag.IS_COMPONENTS_V2,
                    components=[build_container(["Gathering data…"])],
                )
            else:
                initial = await ctx.respond("Gathering data...")
            try:
                message: HMessage = await message_constructor_coro(bot=bot)
            except Exception as e:
                logger.exception(e)
                if cv2:
                    await ctx.edit_response(
                        initial,
                        components=[build_container(["An error occurred!\n" + str(e)])],
                    )
                else:
                    await ctx.edit_response(initial, "An error occurred!\n" + str(e))
                return
            if cv2:
                await ctx.edit_response(initial, components=message.components)
            else:
                await ctx.edit_response(initial, **message.to_message_kwargs())

    return parent_group
