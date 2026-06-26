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

"""Unit tests for the ``ignore_non_src_channels`` listener guard (no DB / network).

The guard runs the wrapped handler only when the message is in a registered mirror
source channel, or — in a test env — when the message lives in one of the configured
test guild(s). The regression these pin down: a precedence bug once made the test-env
bypass apply to *every* guild, so the dev bot reacted to unrelated channels in any
shared server (and then 403'd fetching messages it couldn't read).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import hikari as h
import pytest

from dd.beacon.extensions import mirror
from dd.common import cfg
from dd.common.schemas import MirroredChannel

pytestmark = pytest.mark.asyncio

SRC_CHANNEL = 111
NON_SRC_CHANNEL = 222
TEST_GUILD = 861255712025083904
OTHER_GUILD = 339549444683071488  # e.g. a shared community server


@pytest.fixture(autouse=True)
def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _srcs(*_a: object, **_k: object) -> set[int]:
        return {SRC_CHANNEL}

    monkeypatch.setattr(MirroredChannel, "get_or_fetch_all_srcs", _srcs)
    monkeypatch.setattr(cfg, "test_env", (TEST_GUILD,))


def _create_event(channel_id: int | None, guild_id: int | None) -> MagicMock:
    event = MagicMock(spec=h.MessageCreateEvent)
    event.message = SimpleNamespace(channel_id=channel_id, guild_id=guild_id, id=999)
    return event


async def _processed(event: object) -> bool:
    """Run the guard and report whether the wrapped handler was invoked."""
    called = {"hit": False}

    async def handler(_event: object) -> None:
        called["hit"] = True

    await mirror.ignore_non_src_channels(handler)(event)
    return called["hit"]


async def test_src_channel_processed_even_outside_test_guild() -> None:
    assert await _processed(_create_event(SRC_CHANNEL, OTHER_GUILD)) is True


async def test_test_guild_message_processed() -> None:
    assert await _processed(_create_event(NON_SRC_CHANNEL, TEST_GUILD)) is True


async def test_non_src_non_test_guild_skipped() -> None:
    # The bug's regression test: a non-source channel in a shared server is ignored.
    assert await _processed(_create_event(NON_SRC_CHANNEL, OTHER_GUILD)) is False


async def test_dm_guild_id_none_does_not_raise() -> None:
    assert await _processed(_create_event(NON_SRC_CHANNEL, None)) is False


async def test_uncached_delete_old_message_none_does_not_raise() -> None:
    event = MagicMock(spec=h.MessageDeleteEvent)
    event.old_message = None
    assert await _processed(event) is False


async def test_prod_parity_empty_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # With TEST_ENV unset (prod), only registered sources are processed.
    monkeypatch.setattr(cfg, "test_env", ())
    assert await _processed(_create_event(NON_SRC_CHANNEL, TEST_GUILD)) is False
    assert await _processed(_create_event(SRC_CHANNEL, OTHER_GUILD)) is True
