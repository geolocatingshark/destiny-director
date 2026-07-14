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

# resync_user_commands self-heals a DB-backed command that clashes with a code-defined
# one (e.g. a pre-existing `/dares` row vs. the new world-activity commands): the row is
# deleted and a CRITICAL alert is raised, rather than skipped + re-alerted forever.

import collections
import types
import typing as t

import lightbulb as lb
import pytest

from dd.beacon.extensions import user_commands as uc
from dd.common.schemas import UserCommand

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _client_double() -> lb.Client:
    """A minimal client: resync only touches the invocation mapping before it hits the
    clash branch (which returns early), so a namespace with a well-formed mapping and an
    empty command list is enough."""
    mapping = collections.defaultdict(lambda: collections.defaultdict(lambda: "COLL"))
    return t.cast(
        "lb.Client",
        types.SimpleNamespace(
            _command_invocation_mapping=mapping,
            registered_commands=[],
            register=lambda *a, **k: None,
            unregister=lambda *a, **k: None,
        ),
    )


async def test_resync_deletes_command_clashing_with_code_defined(monkeypatch):
    uc._registered_commands.clear()  # isolate from any leftover module state
    await UserCommand.add_command(
        "dares", description="x", response_type=1, response_data="hi"
    )
    assert await UserCommand.fetch_command("dares") is not None

    # Pretend a code-defined command already owns the name (world-activity `/dares`).
    monkeypatch.setattr(uc, "_code_defined_command_names", lambda client: {"dares"})
    await uc.resync_user_commands(_client_double(), sync=False)

    # The clashing row is gone (self-healed), so it can never shadow the code command.
    assert await UserCommand.fetch_command("dares") is None


async def test_resync_keeps_command_without_clash(monkeypatch):
    uc._registered_commands.clear()
    await UserCommand.add_command(
        "myquip", description="x", response_type=1, response_data="hi"
    )
    monkeypatch.setattr(uc, "_code_defined_command_names", lambda client: set())
    await uc.resync_user_commands(_client_double(), sync=False)

    # No clash → the row survives (it is registered, not deleted).
    assert await UserCommand.fetch_command("myquip") is not None
