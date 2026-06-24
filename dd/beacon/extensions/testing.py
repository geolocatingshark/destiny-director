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
import secrets
import string

import hikari as h
import lightbulb as lb

from ...common import cfg
from ...common.auth import owner_only
from ...common.bot import CachedFetchBot
from ...common.schemas import MirroredChannel
from ...common.utils import guild_scope, parse_channel_ref

# This whole extension only loads in a test environment, matching the lightbulb v2
# behaviour where ``register`` returned early unless ``cfg.test_env`` was set.
loader = lb.Loader(should_load_hook=lambda: bool(cfg.test_env))

testing_group = lb.Group("testing", "Testing group")


_STORM_THRESHOLD = int(cfg.alert_freq_threshold)


def _salt() -> str:
    """A per-invocation alpha token used to isolate a test run's alert signature.

    Uppercase letters only, so it survives the digit-run normalization the alert
    handler applies to message text — every emit in one run shares this salt (and
    so one signature/storm window), while separate runs never collide.
    """
    return "".join(secrets.choice(string.ascii_uppercase) for _ in range(6))


def _log_test_error(operation: str, *, critical: bool, note: str) -> None:
    """Log a synthetic error through ``dd.error`` so it reaches the alerts channel.

    Wraps a real raised ``RuntimeError`` so ``exc_info`` drives the reference code
    and traceback render exactly as a genuine failure would.
    """
    log = logging.getLogger("dd.error")
    try:
        raise RuntimeError(note)
    except RuntimeError:
        (log.critical if critical else log.error)(
            "Test alert requested by owner",
            exc_info=True,
            extra={"dd_operation": operation},
        )


@testing_group.register
class RaiseAlert(
    lb.SlashCommand,
    name="raise_alert",
    description="Fire one test alert into the Discord alerts channel",
    hooks=[owner_only],
):
    level = lb.string(
        "level",
        "Severity to log at (critical pings owners)",
        choices=[lb.Choice("Critical", "critical"), lb.Choice("Error", "error")],
        default="critical",
    )

    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        await ctx.defer(ephemeral=True)
        # Unique salt per run so repeated Error-level tests do not accumulate
        # into one rolling storm window (see RaiseStorm for the rationale).
        _log_test_error(
            "/testing raise_alert",
            critical=self.level == "critical",
            note=f"Test alert (salt {_salt()}) from /testing raise_alert",
        )
        await ctx.respond(
            f"Logged a test {self.level} alert — check the alerts channel."
        )


@testing_group.register
class RaiseStorm(
    lb.SlashCommand,
    name="raise_storm",
    description="Emit a burst of identical errors to trip storm promotion",
    hooks=[owner_only],
):
    count = lb.integer(
        "count",
        f"Errors to emit; the storm threshold is {_STORM_THRESHOLD}",
        default=_STORM_THRESHOLD + 1,
        min_value=1,
    )

    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        await ctx.defer(ephemeral=True)
        # Tag this run with a unique salt so its emits share one signature (and
        # thus one storm window) that is independent of earlier runs — otherwise
        # the handler's rolling per-signature window accumulates across
        # invocations and a sub-threshold batch can inherit a storm. Within the
        # run they dedupe into a single alert; crossing the threshold promotes the
        # Error batch to a CRITICAL "storm".
        salt = _salt()
        for num in range(self.count):
            _log_test_error(
                "/testing raise_storm",
                critical=False,
                note=f"Test storm error (salt {salt}) {num + 1}/{self.count}",
            )
        await ctx.respond(
            f"Logged {self.count} identical errors (threshold {_STORM_THRESHOLD}) "
            "— check the alerts channel."
        )


@testing_group.register
class RaiseUncaught(
    lb.SlashCommand,
    name="raise_uncaught",
    description="Raise an uncaught error to test the command-failure pipeline",
    hooks=[owner_only],
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        # Let this propagate out of the command: lightbulb's pipeline routes it to
        # the shared catch-all handler (``_report_uncaught_command_error`` ->
        # ``log_command_failure``), which forwards it to the alerts channel tagged
        # with this command's name as the failed operation. The interaction itself
        # is left unanswered, so Discord will show it as failed — that is expected.
        raise RuntimeError("Uncaught test failure from /testing raise_uncaught")


# ---------------------------------------------------------------------------
# Mirror pipeline test/cleanup commands
# ---------------------------------------------------------------------------
#
# Stand up bulk mirror targets, drive the beacon mirror pipeline against them,
# and tear them down — covering REVIEW_AND_TESTING.md §4 (progress embeds, the
# majority-failure CRITICAL, the disabled-count escalation). Mirroring itself is
# triggered by posting/editing/deleting in a source channel (in a test env any
# message-create reaches the repeater) or via the `mirror_send` message command;
# these commands only manage channels, mirror wiring, and failure induction.

mirror_group = testing_group.subgroup("mirror", "Mirror pipeline test/cleanup")

# Distinctive default so the prefix sweep can never match real channels.
_DEFAULT_PREFIX = "test90931-"


def _guard_prefix(prefix: str) -> str | None:
    """An error message if ``prefix`` is too broad to bulk-delete safely, else None."""
    if len(prefix) < 4:
        return (
            "Refusing a prefix shorter than 4 characters — too broad to sweep safely."
        )
    return None


async def _fetch_guild(ctx: lb.Context, bot: CachedFetchBot) -> h.Guild:
    if ctx.guild_id is None:
        raise RuntimeError("This command can only be used in a server.")
    return bot.cache.get_guild(ctx.guild_id) or await bot.rest.fetch_guild(ctx.guild_id)


async def _channels_with_prefix(
    bot: CachedFetchBot, guild: h.Guild, prefix: str
) -> list[h.GuildChannel]:
    """All channels in ``guild`` whose name starts with ``prefix``."""
    resolved = [
        bot.cache.get_guild_channel(ch) or await bot.rest.fetch_channel(ch)
        for ch in guild.get_channels()
    ]
    return [
        ch
        for ch in resolved
        if isinstance(ch, h.GuildChannel) and ch.name and ch.name.startswith(prefix)
    ]


@mirror_group.register
class MirrorCreate(
    lb.SlashCommand,
    name="create",
    description="Bulk-create test channels, optionally as mirror targets of a source",
    hooks=[owner_only],
):
    number = lb.integer("number", "How many channels to create", min_value=1)
    # A string (not lb.channel) so a source in another server can be given — the
    # slash-command channel picker only lists channels in the invoking guild.
    follow = lb.string(
        "follow",
        "Source channel to register the new channels as mirror targets of "
        "(link, mention, or id)",
        default="",
    )
    prefix = lb.string("prefix", "Channel name prefix", default=_DEFAULT_PREFIX)

    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        await ctx.defer(ephemeral=True)
        # Refuse a prefix too short to later sweep safely — channels created here are
        # torn down by the prefix-based delete/break commands, so a short prefix would
        # leave them un-cleanable without risking real channels.
        guard = _guard_prefix(self.prefix)
        if guard:
            await ctx.respond(guard)
            return

        try:
            follow_id = (
                parse_channel_ref(self.follow)[0] if self.follow.strip() else None
            )
        except ValueError as e:
            await ctx.respond(str(e))
            return

        guild = await _fetch_guild(ctx, bot)
        for num in range(self.number):
            channel = await guild.create_text_channel(f"{self.prefix}{num}")
            if follow_id is not None:
                await MirroredChannel.add_mirror(
                    follow_id, channel.id, dest_server_id=guild.id, legacy=True
                )

        followed = f" following <#{follow_id}>" if follow_id is not None else ""
        await ctx.respond(
            f"Created {self.number} `{self.prefix}*` channels{followed}. "
            f"Clean up with `/testing mirror delete prefix:{self.prefix}`."
        )


@mirror_group.register
class MirrorBreak(
    lb.SlashCommand,
    name="break",
    description="Delete test channels but keep their mirror rows, to force failures",
    hooks=[owner_only],
):
    prefix = lb.string("prefix", "Channel name prefix", default=_DEFAULT_PREFIX)

    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        await ctx.defer(ephemeral=True)
        guard = _guard_prefix(self.prefix)
        if guard:
            await ctx.respond(guard)
            return

        guild = await _fetch_guild(ctx, bot)
        # Delete the Discord channels but leave their MirroredChannel rows enabled,
        # so the next mirror run fails for every target.
        channels = await _channels_with_prefix(bot, guild, self.prefix)
        for channel in channels:
            await channel.delete()

        await ctx.respond(
            f"Broke {len(channels)} `{self.prefix}*` channels (Discord channels "
            "deleted, mirror rows left enabled). Trigger a mirror on the source "
            "(post/edit, or `mirror_send`) to drive the majority-failure alert, "
            f"then `/testing mirror delete prefix:{self.prefix}`."
        )


@mirror_group.register
class MirrorDelete(
    lb.SlashCommand,
    name="delete",
    description="Delete all prefixed test channels and disable their mirror rows",
    hooks=[owner_only],
):
    prefix = lb.string("prefix", "Channel name prefix", default=_DEFAULT_PREFIX)

    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        await ctx.defer(ephemeral=True)
        guard = _guard_prefix(self.prefix)
        if guard:
            await ctx.respond(guard)
            return

        guild = await _fetch_guild(ctx, bot)
        # Stateless prefix sweep: removes whatever exists now, so it also cleans up
        # after a crashed/partial create or break run.
        channels = await _channels_with_prefix(bot, guild, self.prefix)
        for channel in channels:
            await MirroredChannel.remove_all_mirrors(channel.id)
            await channel.delete()

        await ctx.respond(
            f"Deleted {len(channels)} `{self.prefix}*` channels and disabled their "
            "mirror rows."
        )


@mirror_group.register
class MirrorFailRateBump(
    lb.SlashCommand,
    name="fail_rate_bump",
    description="Bump legacy failure counts so the next run trips the disable alert",
    hooks=[owner_only],
):
    # A string (not lb.channel) so a source in another server can be given — the
    # slash-command channel picker only lists channels in the invoking guild.
    source = lb.string(
        "source", "Source channel whose mirror targets to fail (link, mention, or id)"
    )
    times = lb.integer(
        "times",
        "How many failures to record (disable threshold is 3)",
        default=3,
        min_value=1,
    )

    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        await ctx.defer(ephemeral=True)
        try:
            source_id = parse_channel_ref(self.source)[0]
        except ValueError as e:
            await ctx.respond(str(e))
            return

        dest_ids = await MirroredChannel.fetch_dests(source_id)
        for _ in range(self.times):
            await MirroredChannel.log_legacy_mirror_failure_in_batch(
                source_id, dest_ids
            )

        await ctx.respond(
            f"Recorded {self.times} failures for {len(dest_ids)} targets of "
            f"<#{source_id}>. With `DISABLE_BAD_CHANNELS=true`, the next mirror "
            "run will disable them and emit the disabled-count alert."
        )


loader.command(
    testing_group,
    guilds=guild_scope(*cfg.test_env, cfg.control_discord_server_id),
)
