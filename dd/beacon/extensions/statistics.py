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

import datetime as dt
import logging
import math
import tempfile

import hikari as h
import lightbulb as lb

from ...common import cfg, schemas
from ...common.auth import owner_only
from ...common.bot import CachedFetchBot
from ...common.utils import guild_scope

loader = lb.Loader()

# Top-level command names that are NOT user-facing usage (owner/admin groups).
# Custom user-commands get admin-chosen top-level names that can't collide with
# these (registration rejects collisions), so they're tracked automatically.
_EXCLUDED_ROOTS = frozenset({"autopost", "stats", "mirror", "testing", "command"})


def _should_track(qualified_name: str, command_type: h.CommandType) -> bool:
    """Whether an invocation of this command should be counted.

    Only slash commands (not message/user context menus), and only those whose
    top-level group is not an owner/admin group.
    """
    if command_type is not h.CommandType.SLASH:
        return False
    return qualified_name.split(" ", 1)[0] not in _EXCLUDED_ROOTS


@lb.hook(lb.ExecutionSteps.PRE_INVOKE, skip_when_failed=True)
async def track_command_usage(_pl: lb.ExecutionPipeline, ctx: lb.Context) -> None:
    """Client-wide hook: count each user-facing slash-command invocation.

    Runs at PRE_INVOKE (after CHECKS pass, so owner-gate / permission rejections
    are not counted) and once per leaf-command pipeline. A stats-write failure must
    never break the user's command, so the DB write is swallowed.
    """
    data = ctx.command_data
    if not _should_track(data.qualified_name, data.type):
        return
    try:
        await schemas.CommandUsage.increment(data.qualified_name)
    except Exception:
        logging.getLogger(__name__).warning(
            "Failed to record command usage for %s",
            data.qualified_name,
            exc_info=True,
        )


stats_command_group = lb.Group("stats", "Bot statistics command group")


@stats_command_group.register
class PopulationsCommand(
    lb.SlashCommand,
    name="populations",
    description="Sum of all server populations (Not real time)",
    hooks=[owner_only],
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        await ctx.defer()

        populations: list[
            tuple[int | str, int]
        ] = await schemas.ServerStatistics.fetch_server_populations()
        populations.sort(key=lambda x: x[1], reverse=True)
        top_7 = populations[:7]
        rest = sum(map(lambda x: x[1], populations[7:]))

        for i, (server_id, population) in enumerate(top_7):
            try:
                server = await bot.fetch_guild(int(server_id))
                top_7[i] = (server.name, population)
            except Exception:
                logging.debug(
                    "Could not fetch guild name for %s; using id instead",
                    server_id,
                    exc_info=True,
                )
                # top_7[i] already holds (server_id, population); leave as-is

        # Logarithmic breakdown
        logs = {}
        for _, population in populations:
            log = math.floor(math.log10(population))
            if log in logs:
                logs[log] += 1
            else:
                logs[log] = 1

        log_breakdown_text = ""
        for log_key in sorted(logs.keys()):
            log_breakdown_text += (
                f"\nBetween **{10**log_key:,d}** and "
                + f"**{10 ** (log_key + 1):,d}**: "
                + f"{logs[log_key]:,d}"
            )

        await ctx.respond(
            h.Embed(
                title="Server populations",
                description=""
                + f"\n**Total**: {sum(map(lambda x: x[1], populations)):,d}"
                + "\n**Top 7 servers by population**\n"
                + "\n".join(
                    f"{i + 1}. **{server_name}**: {population:,d}"
                    for i, (server_name, population) in enumerate(top_7)
                )
                + f"\n**Other servers**: {rest:,d}"
                + "\n\n**Logarithmic breakdown of server populations**"
                + log_breakdown_text,
                color=cfg.embed_default_color,
            )
        )


@stats_command_group.register
class ServerListCommand(
    lb.SlashCommand,
    name="server_list",
    hooks=[owner_only],
    description="List of all servers the bot is in",
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
        await ctx.defer()
        percentage_completion = 0
        response = "Working... {}%"
        initial = await ctx.respond(response.format(percentage_completion))

        server_ids: list[int] = await schemas.ServerStatistics.fetch_server_ids()
        server_names = []
        total_servers = len(server_ids)

        for server_number, server_id in enumerate(server_ids):
            try:
                server = await bot.fetch_guild(server_id)
            except (
                h.ForbiddenError,
                h.NotFoundError,
                h.UnauthorizedError,
                h.RateLimitTooLongError,
                h.InternalServerError,
            ):
                server_names.append(f"[{server_id}]")
            else:
                server_names.append(f"{server.name} [{server_id}]")

            if server_number == total_servers - 1 or server_number % 100 == 0:
                percentage_completion = int(100 * server_number / total_servers)
                await ctx.edit_response(initial, response.format(percentage_completion))

        with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt") as server_txt:
            server_txt.write("\n".join(server_names))
            server_txt.seek(0)
            await ctx.edit_response(
                initial, "Completed!", attachment=h.File(server_txt.name)
            )


@stats_command_group.register
class MirrorStatsCommand(
    lb.SlashCommand,
    name="autoposts",
    description="Mirror statistics",
    hooks=[owner_only],
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context):
        """Get the number of destinations for each cfg.followables channel"""
        await ctx.defer()
        dest_legacy_statistics: dict[str, int] = {}
        dest_non_legacy_statistics: dict[str, int] = {}

        for name, channel_id in cfg.followables.items():
            dest_legacy_statistics[name] = await schemas.MirroredChannel.count_dests(
                channel_id, legacy_only=True
            )
            dest_non_legacy_statistics[
                name
            ] = await schemas.MirroredChannel.count_dests(channel_id, legacy_only=False)

        embed = h.Embed(
            title="Autopost statistics",
            description="These stats track all autoposts the bot is aware of. "
            + "It will only be aware of autoposts for servers it is in "
            + "and for channels it can see.",
            color=cfg.embed_default_color,
        )

        for name in dest_legacy_statistics:
            embed.add_field(
                name=name.capitalize(),
                value=f"```Followers : {dest_non_legacy_statistics[name]}\n"
                + f"Mirrors   : {dest_legacy_statistics[name]}```",
                inline=True,
            )

        await ctx.respond(embed)


@stats_command_group.register
class CommandUsageStatsCommand(
    lb.SlashCommand,
    name="commands",
    description="Leaderboard of user-facing command usage",
    hooks=[owner_only],
):
    days = lb.integer(
        "days",
        "Days to look back (0 = all time)",
        default=0,
        min_value=0,
    )

    @lb.invoke
    async def invoke(self, ctx: lb.Context):
        await ctx.defer()
        since = (
            dt.datetime.now(tz=dt.UTC).date() - dt.timedelta(days=self.days)
            if self.days
            else None
        )
        totals = await schemas.CommandUsage.fetch_totals(since=since)

        window = f"last {self.days} days" if self.days else "all time"
        if not totals:
            description = "No command usage recorded yet."
        else:
            description = "\n".join(
                f"{i + 1}. **/{name}**: {count:,d}"
                for i, (name, count) in enumerate(totals[:25])
            )

        await ctx.respond(
            h.Embed(
                title=f"Command usage ({window})",
                description=description,
                color=cfg.embed_default_color,
            )
        )


loader.command(stats_command_group, guilds=guild_scope(cfg.control_discord_server_id))
