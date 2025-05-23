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

import typing as t
from random import randint
from typing import Optional

import hikari as h
import lightbulb as lb

from ...common import cfg
from ...common.schemas import AsyncSession, MirroredChannel, db_session
from ...common.utils import ensure_session
from .. import utils
from ..bot import CachedFetchBot, UserCommandBot

# Permissions that allow users to manage autoposts in a guild
end_user_allowed_perms = (
    h.Permissions.MANAGE_WEBHOOKS,
    h.Permissions.MANAGE_GUILD,
    h.Permissions.MANAGE_CHANNELS,
    h.Permissions.ADMINISTRATOR,
)


def bot_missing_permissions_embed(bot_owner: h.User):
    return h.Embed(
        title="Missing Permissions",
        description="The bot is missing permissions in this channel.\n"
        + "Please make sure it has the following permissions:"
        + "```\n"
        + "- View Channel\n"
        + "- Manage Webhooks\n"
        + "- Send Messages\n"
        + "```\n"
        + "If you are still having issues, please contact me on discord!\n",
        color=cfg.embed_error_color,
    ).set_footer(
        f"@{bot_owner.username}",
        icon=bot_owner.avatar_url or bot_owner.default_avatar_url,
    )


autopost_command_group = lb.command(name="autopost", description="Autopost control")(
    lb.implements(lb.SlashCommandGroup)(lambda: None)
)
autopost_command_group = lb.set_help(
    text="Each also autopost has the option to enable pings "
    "with the `ping_role` option. Mention (@) the desired role "
    "when enabling autoposts to enable pings."
)(autopost_command_group)


async def pre_start(event: h.StartingEvent):
    event.app.command(autopost_command_group)


async def enable_non_legacy_mirror(
    bot: CachedFetchBot, followable_channel: int, ctx: lb.Context, session: AsyncSession
):
    await bot.rest.follow_channel(followable_channel, ctx.channel_id)
    await MirroredChannel.add_mirror(
        followable_channel,
        ctx.channel_id,
        ctx.guild_id,
        False,
        session=session,
    )


async def enable_legacy_mirror(
    bot: CachedFetchBot,
    followable_channel: int,
    ctx: lb.Context,
    role: h.Role,
    session: AsyncSession,
):
    await (
        await (
            await bot.fetch_channel(
                ctx.channel_id,
            )
        ).send("Test message :)")
    ).delete()

    await MirroredChannel.add_mirror(
        followable_channel,
        ctx.channel_id,
        ctx.guild_id,
        True,
        role_mention_id=(role.id if role else 0),
        session=session,
    )


async def unfollow_channel(bot: CachedFetchBot, news_channel: int, target_channel: int):
    for hook in await bot.rest.fetch_channel_webhooks(
        await bot.fetch_channel(target_channel)
    ):
        if (
            isinstance(hook, h.ChannelFollowerWebhook)
            and hook.source_channel
            and hook.source_channel.id == news_channel
        ):
            await bot.rest.delete_webhook(hook)


async def disable_mirror(
    ctx: lb.Context, followable_channel: int, bot: CachedFetchBot, session: AsyncSession
):
    # Check if this is a legacy mirror, and if so, remove it and return
    if int(ctx.channel_id) in (await MirroredChannel.fetch_dests(followable_channel)):
        await MirroredChannel.remove_mirror(
            followable_channel, ctx.channel_id, session=session
        )

    else:
        # If this is not a legacy mirror, then we need to delete the webhook for it

        # Fetch and delete follow based webhooks and filter for our channel as a
        # source
        await unfollow_channel(
            bot=bot, news_channel=followable_channel, target_channel=ctx.channel_id
        )

        # Also remove the mirror
        await MirroredChannel.remove_mirror(
            followable_channel, ctx.channel_id, session=session
        )


def follow_control_command_maker(
    followable_channel: int,
    autoposts_name: str,
    autoposts_friendly_name: str,
    autoposts_desc: str,
):
    """Create a follow control command for a given followable channel

    Args:
        followable_channel (int): The channel ID of the followable channel
        autoposts_name (str): The name of the autoposts command
        autoposts_friendly_name (str): The friendly name to show users for
            the autoposts command. Must be singular and correctly capitalized
            ie first letter capitalized, rest lower case
        autoposts_desc (str): The description for the autoposts command
    """

    @lb.option(
        "option",
        "Enabled or disabled",
        choices=[
            h.CommandChoice(name="Enable", value=1),
            h.CommandChoice(name="Disable", value=0),
        ],
        default=True,
        # Note: Type bool does not allow the choice names to appear for
        # the user, so we use int instead, unsure if this is a lightbulb bug
        type=int,
    )
    @lb.option(
        "ping_role",
        "An optional role to ping when autoposting",
        type=h.Role,
        default=0,
    )
    @lb.command(autoposts_name, autoposts_desc, pass_options=True, auto_defer=True)
    @lb.implements(lb.SlashSubCommand)
    @ensure_session(db_session)
    async def follow_control(
        ctx: lb.Context,
        option: int,
        ping_role: h.Role,
        session: Optional[AsyncSession] = None,
    ):
        option = bool(option)
        bot: t.Union[CachedFetchBot, UserCommandBot] = ctx.bot
        try:
            try:
                # Note: Using the cache here seems to result in utils.check_invoker_has_perms
                # failing if bot.rest.fetch_channel returns a forbidden error later due to
                # what I am assuming is a change in permissions after the cache is initially
                # populated
                await bot.rest.fetch_channel(ctx.channel_id)
            except h.ForbiddenError:
                bot_owner = await bot.fetch_owner()
                await ctx.respond(bot_missing_permissions_embed(bot_owner))
                return

            if not (
                await utils.check_invoker_is_owner(ctx)
                or await utils.check_invoker_has_perms(ctx, end_user_allowed_perms)
            ):
                bot_owner = await bot.fetch_owner()
                await ctx.respond(
                    h.Embed(
                        title="Insufficient permissions",
                        description="You have insufficient permissions to use this command.\n"
                        + "Any one of the following permissions is needed:\n```\n"
                        + "- Manage Webhooks\n"
                        + "- Manage Guild\n"
                        + "- Manage Channel\n"
                        + "- Administrator\n```\n"
                        + "Make sure that you have this permission in this channel and not "
                        + "just in this guild\n"
                        + "Feel free to contact me on discord if you are having issues!\n",
                        color=cfg.embed_error_color,
                    ).set_footer(
                        f"@{bot_owner.username}",
                        icon=bot_owner.avatar_url or bot_owner.default_avatar_url,
                    )
                )
                return

            try:
                if option:
                    # If we are enabling autoposts:
                    try:
                        if ping_role:
                            raise ValueError(
                                "Role pings are not supported by new style mirrors"
                            )
                        await enable_non_legacy_mirror(
                            bot=bot,
                            followable_channel=followable_channel,
                            ctx=ctx,
                            session=session,
                        )
                    except h.BadRequestError as e:
                        if (
                            "cannot execute action on this channel type"
                            in str(e.args).lower()
                        ):
                            # If this is an announce channel, then the above error is thrown
                            # In this case, add a legacy mirror instead

                            # Test sending a message to the channel before adding the mirror
                            await enable_legacy_mirror(
                                bot=bot,
                                followable_channel=followable_channel,
                                ctx=ctx,
                                role=ping_role,
                                session=session,
                            )
                        else:
                            raise e
                    except ValueError as e:
                        if (
                            "role pings are not supported by new style mirrors"
                            in str(e.args).lower()
                        ):
                            await enable_legacy_mirror(
                                bot=bot,
                                followable_channel=followable_channel,
                                ctx=ctx,
                                role=ping_role,
                                session=session,
                            )

                            try:
                                await unfollow_channel(
                                    bot=bot,
                                    news_channel=followable_channel,
                                    target_channel=ctx.channel_id,
                                )
                            except h.ForbiddenError as e2:
                                if (
                                    "missing permissions" in str(e2.args).lower()
                                    or "missing access" in str(e2.args).lower()
                                ):
                                    # If we are missing permissions, then we can't delete the webhook
                                    # In this case, notify the user with a list of possibly missing
                                    # permissions
                                    bot_owner = await bot.fetch_owner()
                                    await ctx.respond(
                                        bot_missing_permissions_embed(bot_owner)
                                    )
                                    return
                                else:
                                    raise e2
                            except h.NotFoundError as e2:
                                if "unknown channel" in str(e2.args).lower():
                                    # In case we cannot fetch the webhooks part of the channel
                                    # we get a not found error. This happens in forum channels
                                    # we can safely ignore this error since its impossible for
                                    # users to be subscribed through webhooks in these types of
                                    # channels
                                    pass
                                else:
                                    raise e2
                        else:
                            raise e
                else:
                    await disable_mirror(
                        ctx=ctx,
                        followable_channel=followable_channel,
                        bot=bot,
                        session=session,
                    )

            except h.ForbiddenError as e:
                if (
                    "missing permissions" in str(e.args).lower()
                    or "missing access" in str(e.args).lower()
                ):
                    # If we are missing permissions, then we can't delete the webhook
                    # In this case, notify the user with a list of possibly missing
                    # permissions
                    bot_owner = await bot.fetch_owner()
                    await ctx.respond(bot_missing_permissions_embed(bot_owner))
                    return
                else:
                    raise e
        except Exception as e:
            error_reference = randint(1000000, 9999999)
            bot_owner = await bot.fetch_owner()
            await ctx.respond(
                h.Embed(
                    title="Pardon our dust!",
                    description="An error occurred while trying to update autopost settings. "
                    + "Please contact "
                    + "me **(username at the bottom of the embed)** with the "
                    + f"error reference `{error_reference}` and we will fix this "
                    + "for you.",
                    color=cfg.embed_error_color,
                ).set_footer(
                    f"@{bot_owner.username}",
                    icon=bot_owner.avatar_url or bot_owner.default_avatar_url,
                )
            )
            await utils.discord_error_logger(bot, e, error_reference)
            raise e
        else:
            await ctx.respond(
                h.Embed(
                    title=f"{autoposts_friendly_name} autoposts "
                    + ("enabled" if option else "disabled")
                    + "!",
                    color=cfg.embed_default_color,
                )
            )

    return follow_control


def register(bot: lb.BotApp):
    bot.listen()(pre_start)
