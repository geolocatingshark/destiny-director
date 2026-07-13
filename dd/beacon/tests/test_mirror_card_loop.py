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

"""Unit tests for the progress-card update loop (``_card_loop``).

The loop re-renders a card from the ledger ``state_counts`` and, on completion, is the
*only* caller of ``_log_run_summary`` — the thing that pages a failed run to the alerts
channel. It must: log the summary exactly once when the run completes, give up (without
a summary) after too many failed card edits or past the lifetime cap, and always release
the cancel menu.
"""

from time import perf_counter
from unittest.mock import AsyncMock, MagicMock

import pytest

from dd.beacon.extensions import mirror
from dd.beacon.mirror_core import MirrorOperationType, RunCounts, RunView
from dd.common.schemas import DeliveryState

pytestmark = pytest.mark.asyncio


def _view() -> RunView:
    view = RunView(
        op=MirrorOperationType.SEND,
        src_ch_id=1,
        src_msg_id=99,
        start_time=perf_counter(),
    )
    view.counts = RunCounts()
    return view


def _log_message() -> MagicMock:
    msg = MagicMock()
    msg.edit = AsyncMock()
    return msg


async def test_logs_summary_once_on_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    # All delivered, none pending → complete on the first tick.
    monkeypatch.setattr(
        mirror.MirrorDelivery,
        "state_counts",
        AsyncMock(return_value={DeliveryState.DELIVERED.value: 3}),
    )
    summary = MagicMock()
    monkeypatch.setattr(mirror, "_log_run_summary", summary)
    log_message = _log_message()

    await mirror._card_loop(log_message, _view(), lambda **_k: [], None)

    summary.assert_called_once()
    log_message.edit.assert_awaited()  # a final render went out


async def test_gives_up_after_repeated_edit_failures_without_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mirror.MirrorDelivery,
        "state_counts",
        AsyncMock(return_value={DeliveryState.PENDING.value: 3}),  # never completes
    )
    monkeypatch.setattr(mirror.aio, "sleep", AsyncMock())  # instant backoff
    summary = MagicMock()
    monkeypatch.setattr(mirror, "_log_run_summary", summary)
    log_message = _log_message()
    log_message.edit = AsyncMock(side_effect=RuntimeError("card deleted"))

    await mirror._card_loop(log_message, _view(), lambda **_k: [], None)  # returns

    assert log_message.edit.await_count == mirror._PROGRESS_UPDATE_MAX_FAILS
    summary.assert_not_called()  # gave up on the card, did not page a summary


async def test_stops_at_lifetime_cap_without_summary_when_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mirror, "_CARD_MAX_LIFETIME", -1)  # already past → final now
    monkeypatch.setattr(
        mirror.MirrorDelivery,
        "state_counts",
        AsyncMock(return_value={DeliveryState.PENDING.value: 3}),  # not complete
    )
    summary = MagicMock()
    monkeypatch.setattr(mirror, "_log_run_summary", summary)
    log_message = _log_message()

    await mirror._card_loop(log_message, _view(), lambda **_k: [], None)

    log_message.edit.assert_awaited_once()  # one final render
    summary.assert_not_called()  # a capped-but-incomplete run is not summarised


async def test_releases_cancel_menu_on_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        mirror.MirrorDelivery,
        "state_counts",
        AsyncMock(return_value={DeliveryState.DELIVERED.value: 1}),
    )
    monkeypatch.setattr(mirror, "_log_run_summary", MagicMock())
    menu = MagicMock()

    await mirror._card_loop(_log_message(), _view(), lambda **_k: [], menu)

    menu.stop_interacting.assert_called_once()
