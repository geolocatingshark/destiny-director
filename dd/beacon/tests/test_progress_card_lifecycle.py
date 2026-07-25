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

"""Unit tests for ``start_progress_card``'s registry bookkeeping (``_cards``).

Complements the atomic-supersede test with the two other bookkeeping invariants a
supersede refactor must keep: a fresh source registers cleanly, and a card task that
runs to completion evicts *itself* from ``_cards`` (via its done-callback) so the map
never leaks finished tasks — without any done-callback ever evicting a newer winner.
"""

import asyncio as aio
from time import perf_counter
from unittest.mock import MagicMock

import pytest

from dd.beacon.extensions import mirror
from dd.beacon.mirror_core import MirrorOperationType, RunView

pytestmark = pytest.mark.asyncio

SRC = 5150


def _view() -> RunView:
    return RunView(
        op=MirrorOperationType.SEND,
        src_ch_id=1,
        src_msg_id=SRC,
        start_time=perf_counter(),
    )


async def test_registers_when_no_prior_card(monkeypatch: pytest.MonkeyPatch) -> None:
    mirror._cards.pop(SRC, None)

    async def fake_run_card(_bot: object, _view: RunView, **_kw: object) -> None:
        await aio.sleep(3600)

    monkeypatch.setattr(mirror, "_run_card", fake_run_card)

    await mirror.start_progress_card(MagicMock(), _view())
    try:
        assert SRC in mirror._cards
    finally:
        mirror._cards[SRC].cancel()
        mirror._cards.pop(SRC, None)


async def test_completed_task_self_evicts_from_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mirror._cards.pop(SRC, None)

    async def fake_run_card(_bot: object, _view: RunView, **_kw: object) -> None:
        return  # completes immediately

    monkeypatch.setattr(mirror, "_run_card", fake_run_card)

    await mirror.start_progress_card(MagicMock(), _view())
    task = mirror._cards[SRC]
    await task  # let the card body finish
    await aio.sleep(0)  # let the done-callback (call_soon) run

    assert SRC not in mirror._cards  # finished task removed itself
