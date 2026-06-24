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

"""``/distortion`` — which Destiny 2 destination is currently distorted.

Distortions make one destination "distorted" each hour, cycling through 7
destinations on a 7-hour loop. Bungie does not expose the active distortion as a
clean API field, so this is computed from a known cycle (no Bungie API / manifest
call), like ``/weekly reset`` and ``/source_code``. If Bungie ever realigns the
cycle, only ``REFERENCE_DATE`` (or the destination order) needs updating.
"""

import datetime as dt

import lightbulb as lb

loader = lb.Loader()

# Distortion order (index 0->6), then repeats.
DISTORTION_DESTINATIONS: tuple[str, ...] = (
    "Cosmodrome",
    "European Dead Zone",
    "Dreaming City",
    "Savathûn's Throne World",
    "Moon",
    "Europa",
    "Nessus",
)

# Start of a Cosmodrome hour (index 0). Derived from the community tracker's sample
# (unix 1781446950 -> Nessus) re-expressed as a clean anchor; verified ref+0h ->
# Cosmodrome, ref+6h -> Nessus, ref+7h -> Cosmodrome, and 1781446950 -> Nessus.
# Source: https://github.com/MelecaZane/d2-distortions
REFERENCE_DATE = dt.datetime(2026, 6, 14, 8, tzinfo=dt.UTC)


def distortion_at(now: dt.datetime) -> tuple[str, str, dt.timedelta]:
    """Return ``(current_destination, next_destination, time_until_rotation)``.

    Integer-floor hourly indexing against ``REFERENCE_DATE`` (tz-aware UTC).
    """
    hours = int((now - REFERENCE_DATE).total_seconds()) // 3600
    idx = hours % len(DISTORTION_DESTINATIONS)
    next_idx = (idx + 1) % len(DISTORTION_DESTINATIONS)
    next_boundary = REFERENCE_DATE + dt.timedelta(hours=hours + 1)
    return (
        DISTORTION_DESTINATIONS[idx],
        DISTORTION_DESTINATIONS[next_idx],
        next_boundary - now,
    )


def _format_countdown(td: dt.timedelta) -> str:
    """Format a positive timedelta as ``Hh Mm`` (or ``Mm`` under an hour)."""
    total_minutes = int(td.total_seconds()) // 60
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


class Distortion(
    lb.SlashCommand,
    name="distortion",
    description="See which Destiny 2 destination is currently distorted",
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context):
        current, upcoming, until = distortion_at(dt.datetime.now(dt.UTC))
        await ctx.respond(
            f"**Distorted now:** {current}\n"
            f"**Up next:** {upcoming} (in {_format_countdown(until)})\n"
            "_Rotation is computed from a known cycle; may drift if Bungie "
            "realigns it._"
        )


loader.command(Distortion)  # global; no guilds= kwarg
