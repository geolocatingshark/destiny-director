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

"""Unit tests for :func:`mirror._ledger_write_with_retry`.

Covers the bounded transient-retry loop that guards a gateway handler's single
ledger write: transient-then-success, permanent short-circuit, and cap exhaustion.
"""

from unittest.mock import AsyncMock

import hikari as h
import pytest

from dd.beacon.extensions import mirror
from dd.common.utils import ErrorClass, classify_error

pytestmark = pytest.mark.asyncio


async def test_transient_then_success(monkeypatch):
    """Two transient blips then a value: retried and the value returned, no alert."""
    sleep = AsyncMock()
    monkeypatch.setattr(mirror.aio, "sleep", sleep)
    error_logger = AsyncMock()
    monkeypatch.setattr(mirror, "discord_error_logger", error_logger)

    sentinel = object()
    op = AsyncMock(
        side_effect=[ConnectionError("blip"), ConnectionError("blip"), sentinel]
    )

    result = await mirror._ledger_write_with_retry("send", op)

    assert result is sentinel
    assert op.await_count == 3
    error_logger.assert_not_awaited()


async def test_permanent_short_circuits(monkeypatch):
    """A permanent error alerts once and returns ``None`` without retrying."""
    sleep = AsyncMock()
    monkeypatch.setattr(mirror.aio, "sleep", sleep)
    error_logger = AsyncMock()
    monkeypatch.setattr(mirror, "discord_error_logger", error_logger)

    forbidden = h.ForbiddenError(
        url="x", headers={}, raw_body="", message="no", code=50001
    )
    # Sanity-check the classification this test relies on.
    assert classify_error(forbidden) is ErrorClass.PERMANENT

    op = AsyncMock(side_effect=forbidden)

    result = await mirror._ledger_write_with_retry("send", op)

    assert result is None
    assert op.await_count == 1
    error_logger.assert_awaited_once()


async def test_cap_exhaustion(monkeypatch):
    """A never-clearing transient failure gives up after the try cap and alerts once."""
    sleep = AsyncMock()
    monkeypatch.setattr(mirror.aio, "sleep", sleep)
    error_logger = AsyncMock()
    monkeypatch.setattr(mirror, "discord_error_logger", error_logger)

    op = AsyncMock(side_effect=ConnectionError("down"))

    result = await mirror._ledger_write_with_retry("send", op)

    assert result is None
    assert op.await_count == mirror._HANDLER_DB_MAX_TRIES
    error_logger.assert_awaited_once()
