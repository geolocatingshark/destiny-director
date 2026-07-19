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
from collections import defaultdict

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


async def _snapshot_autopost_reach(followables: dict[str, int] | None = None) -> None:
    """Snapshot today's active autopost reach into ``AutopostDailyStat``.

    Counts enabled destinations per feed, split into "follow" (native Discord
    channel-follows, i.e. non-legacy) and "mirror" (legacy mirrored channels). The write
    is an idempotent overwrite, so running this more than once a day — including at
    every boot — simply refreshes today's value rather than double-counting.
    """
    followables = cfg.followables if followables is None else followables
    today = dt.datetime.now(tz=dt.UTC).date()
    for feed, src_id in followables.items():
        follows = await schemas.MirroredChannel.count_dests(src_id, legacy_only=False)
        mirrors = await schemas.MirroredChannel.count_dests(src_id, legacy_only=True)
        await schemas.AutopostDailyStat.record(today, feed, "follow", follows)
        await schemas.AutopostDailyStat.record(today, feed, "mirror", mirrors)


@loader.task(lb.uniformtrigger(hours=24, wait_first=False), max_failures=-1)
async def snapshot_autopost_reach() -> None:
    # Runs daily and once at boot (wait_first=False) so a redeploy always writes the
    # current day's snapshot; the idempotent upsert makes repeated runs safe.
    await _snapshot_autopost_reach()


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


# --------------------------------------------------------------------------------------
# Text-chart helpers for the command-usage view (pure; unit-tested in
# dd/beacon/tests/test_command_stats.py). Unicode block glyphs align inside a fenced
# code block, so no charting dep is needed (keeps the Termux/Android build clean).
# --------------------------------------------------------------------------------------

_BAR_FULL = "█"
_BAR_EMPTY = "░"
_SPARK_LEVELS = "▁▂▃▄▅▆▇█"
_NAME_WIDTH = 14
_BAR_WIDTH = 11
_SPARK_WIDTH = 7
_TOP_N = 15


def _bar(value: int, max_value: int, width: int = _BAR_WIDTH) -> str:
    """Proportional block bar, width cells wide; any positive value fills >= 1 cell."""
    if max_value <= 0 or value <= 0:
        return _BAR_EMPTY * width
    filled = max(1, min(width, round(width * value / max_value)))
    return _BAR_FULL * filled + _BAR_EMPTY * (width - filled)


def _downsample(series: list[int], width: int) -> list[int]:
    """Sum series into at most width consecutive buckets (returned as-is if shorter)."""
    if len(series) <= width:
        return list(series)
    buckets = [0] * width
    n = len(series)
    for i, value in enumerate(series):
        buckets[min(width - 1, i * width // n)] += value
    return buckets


def _sparkline(series: list[int], width: int = _SPARK_WIDTH) -> str:
    """Sparkline scaled to the series' own peak; flat baseline when all-zero/empty."""
    buckets = _downsample(series, width) if series else [0]
    peak = max(buckets)
    if peak <= 0:
        return _SPARK_LEVELS[0] * len(buckets)
    top = len(_SPARK_LEVELS) - 1
    return "".join(_SPARK_LEVELS[round(top * value / peak)] for value in buckets)


def _delta(current: int, previous: int) -> str:
    """Trend vs previous window: up/down %, '→0%' if unchanged, 'new' if no prior."""
    if previous <= 0:
        return "new" if current > 0 else "—"
    pct = round((current - previous) / previous * 100)
    if pct > 0:
        return f"↑{pct}%"
    if pct < 0:
        return f"↓{abs(pct)}%"
    return "→0%"


def _truncate(name: str, width: int = _NAME_WIDTH) -> str:
    """Ellipsize a name longer than width (the format spec does the padding)."""
    return name if len(name) <= width else name[: width - 1] + "…"


def _build_command_chart(
    rows: list[tuple[str, dt.date, int]],
    *,
    today: dt.date,
    window_days: int,
    top_n: int = _TOP_N,
) -> str:
    """Aligned bars + count + sparkline + trend from daily ``(name, date, count)`` rows.

    ``rows`` must cover the current window ``[today-window_days+1, today]`` and the
    equal preceding window. Returns "" when no command has usage in the current window.
    """
    cur_start = today - dt.timedelta(days=window_days - 1)
    prev_start = today - dt.timedelta(days=2 * window_days - 1)

    current: dict[str, int] = defaultdict(int)
    previous: dict[str, int] = defaultdict(int)
    daily: dict[str, dict[dt.date, int]] = defaultdict(lambda: defaultdict(int))
    for name, day, count in rows:
        if cur_start <= day <= today:
            current[name] += count
            daily[name][day] += count
        elif prev_start <= day < cur_start:
            previous[name] += count

    ranked = sorted(
        (name for name in current if current[name] > 0),
        key=lambda name: current[name],
        reverse=True,
    )[:top_n]
    if not ranked:
        return ""

    max_cur = current[ranked[0]]
    count_width = len(f"{max_cur:,d}")
    window_dates = [cur_start + dt.timedelta(days=i) for i in range(window_days)]

    lines: list[str] = []
    for name in ranked:
        cur = current[name]
        series = [daily[name].get(day, 0) for day in window_dates]
        lines.append(
            f"/{_truncate(name):<{_NAME_WIDTH}} "
            f"{_bar(cur, max_cur)} "
            f"{cur:>{count_width},d}  "
            f"{_sparkline(series)}  "
            f"{_delta(cur, previous.get(name, 0))}"
        )
    return "\n".join(lines)


def _build_totals_chart(totals: list[tuple[str, int]], *, top_n: int = _TOP_N) -> str:
    """All-time bars + counts (no trend: there is no prior window to compare)."""
    top = [(name, count) for name, count in totals if count > 0][:top_n]
    if not top:
        return ""
    max_count = top[0][1]
    count_width = len(f"{max_count:,d}")
    lines = [
        f"/{_truncate(name):<{_NAME_WIDTH}} {_bar(count, max_count)} "
        f"{count:>{count_width},d}"
        for name, count in top
    ]
    return "\n".join(lines)


@stats_command_group.register
class CommandUsageStatsCommand(
    lb.SlashCommand,
    name="commands",
    description="Leaderboard of user-facing command usage, with trend",
    hooks=[owner_only],
):
    days = lb.integer(
        "days",
        "Days to look back; 0 = all-time leaderboard (no trend)",
        default=7,
        min_value=0,
        max_value=365,
    )

    @lb.invoke
    async def invoke(self, ctx: lb.Context):
        await ctx.defer()
        today = dt.datetime.now(tz=dt.UTC).date()

        if self.days:
            since = today - dt.timedelta(days=2 * self.days - 1)
            rows = await schemas.CommandUsage.fetch_daily(since=since)
            chart = _build_command_chart(rows, today=today, window_days=self.days)
            title = f"Command usage — last {self.days}d (vs previous {self.days}d)"
            empty = f"No command usage recorded for the last {self.days} days."
        else:
            totals = await schemas.CommandUsage.fetch_totals()
            chart = _build_totals_chart(totals)
            title = "Command usage — all time"
            empty = "No command usage recorded yet."

        description = f"```\n{chart}\n```" if chart else empty
        await ctx.respond(
            h.Embed(title=title, description=description, color=cfg.embed_default_color)
        )


loader.command(stats_command_group, guilds=guild_scope(cfg.control_discord_server_id))
