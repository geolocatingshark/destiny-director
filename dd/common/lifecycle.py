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

"""Process lifecycle (stop / restart) shared by both bots.

Termination must work identically from a command invoke **and** a component (button)
callback. hikari runs interaction callbacks as fire-and-forget tasks whose wrapper
drops ``SystemExit``, so ``sys.exit`` raised there is silently swallowed. Instead:
record the desired exit code, schedule ``bot.close()`` on the loop (not awaited inline,
so the interaction reply lands first), let ``bot.run()`` return, and exit on the main
thread via :func:`consume_exit_code` at the end of each ``__main__``.

Railway contract: a clean ``exit 0`` stays down (under an ON_FAILURE restart policy);
a non-zero exit is restarted.
"""

import asyncio

import hikari as h

STOP_EXIT_CODE = 0
RESTART_EXIT_CODE = 1

_desired_exit_code: int | None = None
# Hold a reference to the scheduled close task so it isn't garbage collected mid-flight.
_shutdown_task: asyncio.Task[None] | None = None


async def request_shutdown(bot: h.GatewayBot, exit_code: int) -> None:
    """Record the desired process exit code and schedule a clean gateway shutdown.

    ``bot.close()`` is scheduled rather than awaited inline so the calling interaction
    callback can finish replying before the REST client is torn down. Once ``close()``
    unwinds the gateway, ``bot.run()`` returns and ``__main__`` exits with the recorded
    code (see :func:`consume_exit_code`).
    """
    global _desired_exit_code, _shutdown_task
    _desired_exit_code = exit_code
    _shutdown_task = asyncio.get_running_loop().create_task(bot.close())


def consume_exit_code() -> int:
    """Return the exit code requested via :func:`request_shutdown` (``0`` if none)."""
    return _desired_exit_code if _desired_exit_code is not None else 0
