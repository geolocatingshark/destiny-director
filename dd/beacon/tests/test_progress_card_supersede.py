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

"""Unit test for ``start_progress_card``'s atomic supersede (one live card per source).

Two near-simultaneous starts for the same source must not both survive: the pop+cancel
of the old task and the register of the new one happen with no ``await`` between them,
so the older card is always cancelled before the newer one takes over.
"""

import asyncio as aio
from time import perf_counter
from unittest.mock import MagicMock

import pytest

from dd.beacon.extensions import mirror
from dd.beacon.mirror_core import MirrorOperationType, RunView

pytestmark = pytest.mark.asyncio

SRC = 4242


def _view() -> RunView:
    return RunView(
        op=MirrorOperationType.SEND,
        src_ch_id=1,
        src_msg_id=SRC,
        start_time=perf_counter(),
    )


async def test_start_progress_card_supersedes_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mirror._cards.pop(SRC, None)

    async def fake_run_card(_bot: object, _view: RunView, **_kw: object) -> None:
        await aio.sleep(3600)  # long-lived; a supersede cancels it

    monkeypatch.setattr(mirror, "_run_card", fake_run_card)
    bot = MagicMock()

    await mirror.start_progress_card(bot, _view())
    first = mirror._cards[SRC]
    await mirror.start_progress_card(bot, _view())
    second = mirror._cards[SRC]

    try:
        assert first is not second  # the second start replaced the first
        assert len([k for k in mirror._cards if k == SRC]) == 1  # only one live card
        await aio.sleep(0)  # let the first task's cancellation settle
        assert first.cancelled()  # the superseded card was cancelled
        assert not second.cancelled()  # the current one is still live
        assert mirror._cards[SRC] is second  # its done-callback didn't evict the winner
    finally:
        second.cancel()
        mirror._cards.pop(SRC, None)
