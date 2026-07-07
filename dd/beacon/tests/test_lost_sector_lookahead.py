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

"""Regression for SectorMessages.lookahead covering the full reachable forward range."""

import datetime as dt

import pytest

from dd.beacon.extensions import lost_sector
from dd.hmessage import HMessage

pytestmark = pytest.mark.asyncio


async def test_lookahead_covers_full_reachable_forward_range(monkeypatch):
    # The navigator lets the user reach index lookahead_len (next_disabled at
    # current_page >= lookahead_len), so the lookahead must populate indices
    # 1..lookahead_len — else the last "+lookahead_len days" page is permanently empty.
    lookahead_len = 7
    period = dt.timedelta(days=1)

    async def _fake_load_rotation(*a, **k):
        return lambda date: ["sector"]  # sector_on: every date has data

    async def _fake_format_post(**k):
        return HMessage(components=[])

    monkeypatch.setattr(lost_sector, "load_rotation", _fake_load_rotation)
    monkeypatch.setattr(lost_sector, "format_post", _fake_format_post)

    pages = lost_sector.SectorMessages.__new__(lost_sector.SectorMessages)
    pages.lookahead_len = lookahead_len
    pages.period = period
    pages.bot = type("_Bot", (), {"emoji": {}})()
    pages.no_data_message = HMessage(components=[])

    after = dt.datetime(2026, 7, 7, 17, tzinfo=dt.UTC)  # index 1 (tomorrow)
    result = await pages.lookahead(after)

    expected = {after + period * n for n in range(lookahead_len)}
    assert set(result.keys()) == expected
    assert len(result) == lookahead_len
    # The last reachable forward page (index lookahead_len) is populated.
    assert after + period * (lookahead_len - 1) in result
