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


import enum

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


class MirrorOutcome(enum.Enum):
    """Classification of a hikari error hit while (un)following a channel."""

    MISSING_PERMS = enum.auto()  # bot lacks channel perms → tell the user
    NEEDS_LEGACY = enum.auto()  # announce channel → use a legacy mirror instead
    CHANNEL_GONE = enum.auto()  # no such channel/webhook (e.g. forum) → ignore
    OTHER = enum.auto()  # unclassified → re-raise


# Discord JSON error codes — stable across wording/locale/version, unlike the message
# text the old substring matching relied on.
_OUTCOME_BY_CODE: dict[int, MirrorOutcome] = {
    50001: MirrorOutcome.MISSING_PERMS,  # Missing Access
    50013: MirrorOutcome.MISSING_PERMS,  # Missing Permissions
    50024: MirrorOutcome.NEEDS_LEGACY,  # Cannot execute action on this channel type
    10003: MirrorOutcome.CHANNEL_GONE,  # Unknown Channel
}


def classify_mirror_error(exc: BaseException) -> MirrorOutcome:
    """Classify a hikari error by its Discord error code into a MirrorOutcome.

    Code-based, so it no longer breaks when Discord changes the message wording,
    locale, or API version. A non-hikari error (no ``code``) classifies as OTHER."""
    return _OUTCOME_BY_CODE.get(getattr(exc, "code", None), MirrorOutcome.OTHER)


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


def insufficient_permissions_embed(bot_owners: list[h.User]) -> h.Embed:
    return set_owners_footer(
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
        ),
        bot_owners,
    )


def autopost_error_embed(bot_owners: list[h.User], error_reference: str) -> h.Embed:
    return set_owners_footer(
        h.Embed(
            title="Pardon our dust!",
            description="An error occurred while trying to update autopost settings. "
            + "Please contact me **(username at the bottom of the embed)** with the "
            + f"error reference `{error_reference}` and we will fix this for you.",
            color=cfg.embed_error_color,
        ),
        bot_owners,
    )


async def _respond_autopost_error(
    ctx: lb.Context, bot: CachedFetchBot, e: Exception
) -> None:
    error_reference = await discord_error_logger(e, operation="Autopost")
    await ctx.respond(autopost_error_embed(await bot.fetch_owners(), error_reference))


async def _drop_existing_follow(
    bot: CachedFetchBot, followable_channel: int, target_channel: int
) -> None:
    """Remove any existing follow webhook when switching to a legacy mirror.

    A forum channel has no webhooks to remove (Unknown Channel → CHANNEL_GONE) and is
    ignored; a permissions error propagates so the caller can report MISSING_PERMS."""
    try:
        await unfollow_channel(bot, followable_channel, target_channel)
    except h.NotFoundError as e:
        if classify_mirror_error(e) is not MirrorOutcome.CHANNEL_GONE:
            raise


async def _enable_autopost(
    bot: CachedFetchBot,
    followable_channel: int,
    ctx: lb.Context,
    ping_role: h.Role | None,
    session: AsyncSession,
) -> None:
    """Enable an autopost mirror for ``ctx``'s channel.

    New-style (follow-webhook) mirrors can't ping roles, so when a ping role is
    requested we go straight to a legacy mirror and drop any prior follow webhook.
    Otherwise we try a new-style mirror first and fall back to legacy only when the
    channel can't be followed (announce channel → NEEDS_LEGACY)."""
    if ping_role:
        await enable_legacy_mirror(bot, followable_channel, ctx, ping_role, session)
        await _drop_existing_follow(bot, followable_channel, ctx.channel_id)
        return

    try:
        await enable_non_legacy_mirror(bot, followable_channel, ctx, session)
    except h.BadRequestError as e:
        if classify_mirror_error(e) is not MirrorOutcome.NEEDS_LEGACY:
            raise
        await enable_legacy_mirror(bot, followable_channel, ctx, ping_role, session)


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

            enabling = bool(self.option)
            ping_role: h.Role | None = self.ping_role

            async with db_session() as session, session.begin():
                try:
                    # Preflight 1 — the bot must be able to see the channel. Use the
                    # REST (not the cache): a stale cache can mask a later permissions
                    # change and make the invoker-perms check pass spuriously.
                    try:
                        await bot.rest.fetch_channel(ctx.channel_id)
                    except h.ForbiddenError:
                        await respond_missing_perms(ctx, bot)
                        return

                    # Preflight 2 — the invoker must own the bot or hold a managing
                    # permission in this channel.
                    if not (
                        await check_invoker_is_owner(ctx)
                        or await utils.check_invoker_has_perms(
                            ctx, end_user_allowed_perms
                        )
                    ):
                        await ctx.respond(
                            insufficient_permissions_embed(await bot.fetch_owners())
                        )
                        return

                    # Apply the change. NEEDS_LEGACY (announce channel) and
                    # CHANNEL_GONE (forum webhooks) are resolved inside the helpers;
                    # only MISSING_PERMS and unclassified errors surface here.
                    if enabling:
                        await _enable_autopost(
                            bot, followable_channel, ctx, ping_role, session
                        )
                    else:
                        await disable_mirror(ctx, followable_channel, bot, session)
                except Exception as e:
                    if classify_mirror_error(e) is MirrorOutcome.MISSING_PERMS:
                        await respond_missing_perms(ctx, bot)
                        return
                    await _respond_autopost_error(ctx, bot, e)
                    raise
                else:
                    await ctx.respond(
                        h.Embed(
                            title=f"{autoposts_friendly_name} autoposts "
                            + ("enabled" if enabling else "disabled")
                            + "!",
                            color=cfg.embed_default_color,
                        )
                    )

    return FollowControl


loader.command(autopost_command_group)
