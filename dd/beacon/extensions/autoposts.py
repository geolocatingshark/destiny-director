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


import hikari as h
import lightbulb as lb
from sqlalchemy.ext.asyncio import AsyncSession

from ...common import cfg
from ...common.auth import check_invoker_is_owner
from ...common.bot import CachedFetchBot
from ...common.schemas import MirroredChannel, db_session
from ...common.utils import discord_error_logger
from .. import utils

loader = lb.Loader()

# Permissions that allow users to manage autoposts in a guild
end_user_allowed_perms = [
    h.Permissions.MANAGE_WEBHOOKS,
    h.Permissions.MANAGE_GUILD,
    h.Permissions.MANAGE_CHANNELS,
    h.Permissions.ADMINISTRATOR,
]

# Shared command group that every followable module attaches a subcommand to.
autopost_command_group = lb.Group(
    "autopost",
    "Autopost control (mention a role with ping_role when enabling to also ping it)",
)


def set_owners_footer(embed: h.Embed, bot_owners: list[h.User]) -> h.Embed:
    """Set an embed footer listing the bot owner(s) as points of contact.

    The footer text lists every owner's username; the icon uses the primary
    (first) owner's avatar since footers only support a single icon."""
    return embed.set_footer(
        ", ".join(f"@{owner.username}" for owner in bot_owners),
        icon=bot_owners[0].display_avatar_url,
    )


def bot_missing_permissions_embed(bot_owners: list[h.User]):
    return set_owners_footer(
        h.Embed(
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
        ),
        bot_owners,
    )


def _is_missing_perms(e: Exception) -> bool:
    """True if a hikari error indicates the bot lacks channel permissions.

    NOTE: this matches on the error's message text, which is fragile — see the
    ToDo.md entry for the planned exception-type/error-code state-machine
    rewrite of FollowControl.invoke."""
    args = str(e.args).lower()
    return "missing permissions" in args or "missing access" in args


async def respond_missing_perms(ctx: lb.Context, bot: CachedFetchBot) -> None:
    """Reply with the standard 'bot is missing permissions in this channel'
    embed, listing the bot owner(s) as points of contact."""
    bot_owners = await bot.fetch_owners()
    await ctx.respond(bot_missing_permissions_embed(bot_owners))


async def enable_non_legacy_mirror(
    bot: CachedFetchBot, followable_channel: int, ctx: lb.Context, session: AsyncSession
):
    await bot.rest.follow_channel(followable_channel, ctx.channel_id)
    if ctx.guild_id is None:
        raise RuntimeError("This command can only be used in a server.")
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
    role: h.Role | None,
    session: AsyncSession,
):
    channel = await bot.fetch_channel(ctx.channel_id)
    if not isinstance(channel, h.TextableChannel):
        raise TypeError(f"Channel {ctx.channel_id} is not a textable channel")
    await (await channel.send("Test message :)")).delete()

    if ctx.guild_id is None:
        raise RuntimeError("This command can only be used in a server.")
    await MirroredChannel.add_mirror(
        followable_channel,
        ctx.channel_id,
        ctx.guild_id,
        True,
        role_mention_id=(role.id if role else 0),
        session=session,
    )


async def unfollow_channel(bot: CachedFetchBot, news_channel: int, target_channel: int):
    for hook in await bot.rest.fetch_channel_webhooks(target_channel):
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

    @autopost_command_group.register
    class FollowControl(
        lb.SlashCommand, name=autoposts_name, description=autoposts_desc
    ):
        # Note: Type int (not bool) is used so the choice names appear for the
        # user, matching the lightbulb v2 behaviour.
        option = lb.integer(
            "option",
            "Enabled or disabled",
            choices=[lb.Choice("Enable", 1), lb.Choice("Disable", 0)],
            default=1,
        )
        ping_role = lb.role(
            "ping_role",
            "An optional role to ping when autoposting",
            default=None,
        )

        @lb.invoke
        async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
            await ctx.defer()

            option = bool(self.option)
            ping_role: h.Role | None = self.ping_role

            # SIM117: deliberately kept nested rather than combined into a single
            # `async with` — the body is a large, deeply-nested transaction block and
            # flattening one indent level off it hurts readability more than it helps.
            async with db_session() as session:  # noqa: SIM117
                async with session.begin():
                    try:
                        try:
                            # Note: Using the cache here seems to result in
                            # utils.check_invoker_has_perms failing if
                            # bot.rest.fetch_channel returns a forbidden error later
                            # due to what I am assuming is a change in permissions
                            # after the cache is initially populated
                            await bot.rest.fetch_channel(ctx.channel_id)
                        except h.ForbiddenError:
                            await respond_missing_perms(ctx, bot)
                            return

                        if not (
                            await check_invoker_is_owner(ctx)
                            or await utils.check_invoker_has_perms(
                                ctx, end_user_allowed_perms
                            )
                        ):
                            bot_owners = await bot.fetch_owners()
                            await ctx.respond(
                                set_owners_footer(
                                    h.Embed(
                                        title="Insufficient permissions",
                                        description="You have insufficient "
                                        "permissions to use this command.\n"
                                        + "Any one of the following permissions is "
                                        + "needed:\n```\n"
                                        + "- Manage Webhooks\n"
                                        + "- Manage Guild\n"
                                        + "- Manage Channel\n"
                                        + "- Administrator\n```\n"
                                        + "Make sure that you have this permission "
                                        + "in this channel and not "
                                        + "just in this guild\n"
                                        + "Feel free to contact me on discord if "
                                        + "you are having issues!\n",
                                        color=cfg.embed_error_color,
                                    ),
                                    bot_owners,
                                )
                            )
                            return

                        try:
                            if option:
                                # If we are enabling autoposts:
                                try:
                                    if ping_role:
                                        raise ValueError(
                                            "Role pings are not supported by "
                                            "new style mirrors"
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
                                        # If this is an announce channel, then
                                        # the above error is thrown. In this
                                        # case, add a legacy mirror instead

                                        # Test sending a message to the channel
                                        # before adding the mirror
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
                                        "role pings are not supported by new "
                                        "style mirrors" in str(e.args).lower()
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
                                            if _is_missing_perms(e2):
                                                # Can't delete the webhook without
                                                # permissions; notify the user with
                                                # a list of possibly missing perms.
                                                await respond_missing_perms(ctx, bot)
                                                return
                                            else:
                                                raise e2
                                        except h.NotFoundError as e2:
                                            if (
                                                "unknown channel"
                                                in str(e2.args).lower()
                                            ):
                                                # In case we cannot fetch the
                                                # webhooks part of the channel we
                                                # get a not found error. This
                                                # happens in forum channels. We
                                                # can safely ignore this error
                                                # since its impossible for users
                                                # to be subscribed through
                                                # webhooks in these types of
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
                            if _is_missing_perms(e):
                                # Can't delete the webhook without permissions;
                                # notify the user with a list of possibly missing
                                # perms.
                                await respond_missing_perms(ctx, bot)
                                return
                            else:
                                raise e
                    except Exception as e:
                        error_reference = await discord_error_logger(
                            e, operation="Autopost"
                        )
                        bot_owners = await bot.fetch_owners()
                        await ctx.respond(
                            set_owners_footer(
                                h.Embed(
                                    title="Pardon our dust!",
                                    description="An error occurred while trying to "
                                    "update autopost settings. "
                                    + "Please contact "
                                    + "me **(username at the bottom of the embed)** "
                                    + "with the "
                                    + f"error reference `{error_reference}` and we "
                                    + "will fix this "
                                    + "for you.",
                                    color=cfg.embed_error_color,
                                ),
                                bot_owners,
                            )
                        )
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

    return FollowControl


loader.command(autopost_command_group)
