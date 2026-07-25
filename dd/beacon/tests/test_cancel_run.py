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

"""Unit test for ``_cancel_run`` — the shared cancel path (button + /mirror_cancel).

Cancellation is keyed on ``src_msg_id`` and acts on the ledger, not on any one card
message. That is what makes it safe for two progress cards for the same source to
briefly share the ``dd_mirror_cancel:<src>`` custom_id: whichever card's button fires,
the same per-source PENDING rows are cancelled and the worker is nudged, exactly once.
"""

from time import perf_counter
from unittest.mock import AsyncMock, MagicMock

import pytest

from dd.beacon.extensions import mirror
from dd.beacon.mirror_core import MirrorOperationType, RunView

pytestmark = pytest.mark.asyncio


async def test_cancel_run_is_keyed_on_source_and_nudges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancel_pending = AsyncMock()
    monkeypatch.setattr(mirror.MirrorDelivery, "cancel_pending", cancel_pending)
    worker = MagicMock()
    monkeypatch.setattr(mirror, "mirror_worker", worker)

    view = RunView(
        op=MirrorOperationType.UPDATE,
        src_ch_id=1,
        src_msg_id=909,
        start_time=perf_counter(),
    )
    await mirror._cancel_run(view)

    cancel_pending.assert_awaited_once_with(909)  # per-source, message-agnostic
    worker.nudge.assert_called_once()
