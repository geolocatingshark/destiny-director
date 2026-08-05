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

# Pure tests for the shared bot-administration controller factory — no Discord I/O.

from dd.common.controller import make_controller_group


def test_group_named_after_bot() -> None:
    # Only beacon builds one now — anchor's stop/info moved to its web control panel.
    group = make_controller_group("beacon")
    assert group.name == "beacon"
    assert group.description == "Bot administration"


def test_group_has_stop_info_subcommands() -> None:
    # `restart` was removed 2026-08-04 — it only worked by exiting non-zero, which is a
    # crash to Railway. Assert the exact set so it can't creep back in unnoticed.
    group = make_controller_group("beacon")
    assert set(group.subcommands.keys()) == {"stop", "info"}


def test_each_call_builds_fresh_instances() -> None:
    # Lightbulb command objects carry per-client registration state, so the two bots
    # must not share one group/command instance.
    first = make_controller_group("beacon")
    second = make_controller_group("beacon")
    assert first is not second
    assert first.subcommands["stop"] is not second.subcommands["stop"]
