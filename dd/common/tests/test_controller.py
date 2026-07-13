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

import pytest

from dd.common import cfg
from dd.common.controller import make_controller_group, restarts_enabled


def test_group_named_after_bot() -> None:
    group = make_controller_group("beacon")
    assert group.name == "beacon"
    assert group.description == "Bot administration"


def test_group_has_restart_stop_info_subcommands() -> None:
    group = make_controller_group("anchor")
    assert set(group.subcommands.keys()) == {"restart", "stop", "info"}


def test_each_call_builds_fresh_instances() -> None:
    # Lightbulb command objects carry per-client registration state, so the two bots
    # must not share one group/command instance.
    first = make_controller_group("anchor")
    second = make_controller_group("anchor")
    assert first is not second
    assert first.subcommands["restart"] is not second.subcommands["restart"]


def test_restarts_disabled_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    # Prod is the only config that leaves TEST_ENV empty (cfg.test_env == ()); there a
    # `/restart` non-zero exit risks Railway crash-loop backoff, so it must be refused.
    monkeypatch.setattr(cfg, "test_env", ())
    assert restarts_enabled() is False


def test_restarts_enabled_in_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # A test/dev environment (TEST_ENV set → truthy tuple of guild ids) keeps restart.
    monkeypatch.setattr(cfg, "test_env", (1000000000000000000,))
    assert restarts_enabled() is True
