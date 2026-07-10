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

"""Beacon's bot-administration commands: ``/beacon restart | stop | info``.

Thin wrapper over the shared factory in :mod:`dd.common.controller`. Beacon's client
has no client-wide owner gate, so the per-subcommand ``owner_only`` the factory applies
restricts these to the bot team; this wrapper scopes registration to the control guild
(plus the test guild(s) in a test environment). It passes a ``mirror_check`` so
stop/restart warn + require a DANGER override while mirror operations are in progress.

Note: ``/beacon stop`` exits cleanly. Prod beacon's Railway restart policy was set to
``ON_FAILURE`` on 2026-06-25 (all services uniform), so it stops cleanly everywhere."""

import lightbulb as lb

from ...common import cfg
from ...common.controller import make_controller_group
from ...common.utils import guild_scope
from ..mirror_worker import mirror_worker

loader = lb.Loader()
loader.command(
    make_controller_group(
        "beacon",
        mirror_check=lambda: mirror_worker.in_progress_count,
    ),
    guilds=guild_scope(*cfg.test_env, cfg.control_discord_server_id),
)
