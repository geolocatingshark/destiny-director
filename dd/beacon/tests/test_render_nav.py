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

"""Unit tests for the navigator's pure render pieces (no Discord I/O)."""

import datetime as dt

import hikari as h

from dd.beacon.nav import (
    _NAV_INDICATOR_CUSTOM_ID,
    _NAV_NEXT_CUSTOM_ID,
    _NAV_PREV_CUSTOM_ID,
    NavigatorView,
    build_nav_row,
)
from dd.hmessage import HMessage


def _row(current_page, *, history_len=4, lookahead_len=0, all_disabled=False):
    return build_nav_row(
        current_page=current_page,
        history_len=history_len,
        lookahead_len=lookahead_len,
        date_label="January 1st",
        all_disabled=all_disabled,
    )


# --- build_nav_row ---------------------------------------------------------------


def test_nav_row_custom_ids_and_disabled_date_indicator():
    prev, indicator, nxt = _row(0)
    assert prev.custom_id == _NAV_PREV_CUSTOM_ID
    assert indicator.custom_id == _NAV_INDICATOR_CUSTOM_ID
    assert nxt.custom_id == _NAV_NEXT_CUSTOM_ID
    assert indicator.label == "January 1st"
    assert indicator.is_disabled is True


def test_prev_disabled_exactly_at_history_floor():
    hist = 4
    floor = 1 - hist  # -3: oldest reachable page
    assert _row(floor, history_len=hist)[0].is_disabled is True
    assert _row(floor + 1, history_len=hist)[0].is_disabled is False


def test_next_disabled_exactly_at_lookahead_ceiling():
    look = 2
    assert _row(look, lookahead_len=look)[2].is_disabled is True
    assert _row(look - 1, lookahead_len=look)[2].is_disabled is False


def test_all_disabled_disables_prev_and_next():
    prev, _indicator, nxt = _row(0, history_len=4, lookahead_len=2, all_disabled=True)
    assert prev.is_disabled is True
    assert nxt.is_disabled is True


# --- NavigatorView render (embed vs CV2 page selection) --------------------------


class _FakePages:
    """Minimal NavPages stand-in for exercising NavigatorView's render path."""

    def __init__(self, page: HMessage, *, history_len: int = 4, lookahead_len: int = 0):
        self.history_len = history_len
        self.lookahead_len = lookahead_len
        self._page = page

    def __contains__(self, index: int) -> bool:
        return index == 0

    def __getitem__(self, index: int) -> HMessage:
        return self._page

    def index_to_date(self, index: int) -> dt.datetime:
        return dt.datetime(2024, 1, 2, tzinfo=dt.UTC)


def test_embed_page_renders_as_embed_with_nav_row():
    page = HMessage(embeds=[h.Embed(title="t", description="d")])
    navigator = NavigatorView(pages=_FakePages(page))  # ty: ignore[invalid-argument-type]

    payload = navigator._render()

    assert "flags" not in payload  # embed, not CV2
    assert payload["embeds"] == page.embeds
    assert len(payload["components"]) == 1  # a single nav action row


def test_cv2_page_renders_with_flag_and_container_untouched():
    container = h.impl.ContainerComponentBuilder()
    container.add_text_display("hello")
    page = HMessage(components=[container])
    navigator = NavigatorView(pages=_FakePages(page))  # ty: ignore[invalid-argument-type]

    payload = navigator._render()

    assert payload["flags"] == h.MessageFlag.IS_COMPONENTS_V2
    # The source container is preserved (never mutated) and the nav row is appended.
    assert payload["components"][0] is container
    assert len(payload["components"]) == 2


def test_navigators_use_distinct_instance_button_ids():
    # Regression: shared button custom_ids let a press on one navigator's message be
    # routed by lightbulb to another live navigator's menu, which then edits this
    # message with its own pages. Per-instance ids keep a press on this navigator's
    # message routing only to this navigator's menu.
    page = HMessage(embeds=[h.Embed(title="t", description="d")])
    n1 = NavigatorView(pages=_FakePages(page))  # ty: ignore[invalid-argument-type]
    n2 = NavigatorView(pages=_FakePages(page))  # ty: ignore[invalid-argument-type]

    assert n1._prev_id != n2._prev_id
    assert n1._next_id != n2._next_id

    # The rendered nav row carries THIS instance's ids (matching its own menu).
    row = n1._render()["components"][-1]
    ids = [b.custom_id for b in row.components]
    assert n1._prev_id in ids
    assert n1._next_id in ids
    assert n2._prev_id not in ids
