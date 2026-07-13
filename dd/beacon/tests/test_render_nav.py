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
import http

import hikari as h
import pytest

from dd.beacon.nav import (
    _NAV_INDICATOR_CUSTOM_ID,
    _NAV_NEXT_CUSTOM_ID,
    _NAV_PREV_CUSTOM_ID,
    NavigatorView,
    NavPages,
    build_nav_row,
)
from dd.common import components as dd_components
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


# --- mixed history renders single-mode CV2 (the boundary regression) --------------


class _MultiPages:
    """NavPages stand-in returning a different page per index, with a cv2 flag."""

    def __init__(self, pages_by_index, *, cv2, history_len=4, lookahead_len=0):
        self.cv2 = cv2
        self.history_len = history_len
        self.lookahead_len = lookahead_len
        self._pages = pages_by_index
        self.no_data_message = HMessage(
            components=[dd_components.build_container(["No data here!"])]
        )

    def __contains__(self, index):
        return index in self._pages

    def __getitem__(self, index):
        return self._pages.get(index, self.no_data_message)

    def index_to_date(self, index):
        return dt.datetime(2024, 1, 2, tzinfo=dt.UTC)


def _bare_cv2_pages() -> NavPages:
    pages = NavPages.__new__(NavPages)
    pages.cv2 = True
    pages.no_data_message = HMessage(
        components=[dd_components.build_container(["No data here!"])]
    )
    return pages


def test_mixed_history_renders_every_page_as_cv2():
    # A native-CV2 post and a legacy embed post, each finalized for a cv2 navigator.
    finalizer = _bare_cv2_pages()
    native = h.impl.ContainerComponentBuilder()
    native.add_text_display("live")
    page_native = finalizer._finalize_cv2(HMessage(components=[native]))
    page_from_embed = finalizer._finalize_cv2(
        HMessage(embeds=[h.Embed(title="old", description="post")])
    )

    fake = _MultiPages({0: page_native, -1: page_from_embed}, cv2=True)
    navigator = NavigatorView(pages=fake)  # ty: ignore[invalid-argument-type]

    for idx in (0, -1):
        navigator._current_page = idx
        payload = navigator._render()
        assert payload["flags"] == h.MessageFlag.IS_COMPONENTS_V2
        assert "embeds" not in payload  # never crosses the embed<->CV2 boundary


# --- defensive edit guard --------------------------------------------------------


class _FakeMenuCtx:
    """Records respond() calls; optionally rejects the first like Discord would."""

    def __init__(self, *, fail_first: bool):
        self.calls: list[dict] = []
        self._fail_first = fail_first

    async def respond(self, **kwargs):
        self.calls.append(kwargs)
        if self._fail_first and len(self.calls) == 1:
            raise h.BadRequestError(url="u", headers={}, raw_body="too long")


@pytest.mark.asyncio
async def test_edit_falls_back_to_same_mode_cv2_page_on_bad_request(monkeypatch):
    import dd.beacon.nav as nav

    async def _fake_logger(*a, **k):
        return ("logged", a, k)

    monkeypatch.setattr(nav, "discord_error_logger", _fake_logger)

    container = h.impl.ContainerComponentBuilder()
    container.add_text_display("page")
    fake = _MultiPages({0: HMessage(components=[container])}, cv2=True)
    navigator = NavigatorView(pages=fake)  # ty: ignore[invalid-argument-type]

    mctx = _FakeMenuCtx(fail_first=True)
    await navigator._edit(mctx)  # ty: ignore[invalid-argument-type]

    # First render rejected, second is the CV2 fallback (same mode -> no flag toggle).
    assert len(mctx.calls) == 2
    assert mctx.calls[1]["edit"] is True
    assert mctx.calls[1]["flags"] == h.MessageFlag.IS_COMPONENTS_V2


class _FakeCtx:
    """Records respond() calls; optionally 429s the first, like a media re-download."""

    def __init__(self, *, fail_first: bool):
        self.calls: list[dict] = []
        self._fail_first = fail_first

    async def respond(self, **kwargs):
        self.calls.append(kwargs)
        if self._fail_first and len(self.calls) == 1:
            raise h.ClientHTTPResponseError(
                url="u",
                status=http.HTTPStatus.TOO_MANY_REQUESTS,
                headers={},
                raw_body="rate limited",
            )


@pytest.mark.asyncio
async def test_send_falls_back_on_client_http_error(monkeypatch):
    # The initial open (send) must survive a non-BadRequest client error (e.g. a 429
    # from a streamed media host), not just prev/next — hence the widened except.
    import dd.beacon.nav as nav

    async def _fake_logger(*a, **k):
        return ("logged", a, k)

    monkeypatch.setattr(nav, "discord_error_logger", _fake_logger)

    container = h.impl.ContainerComponentBuilder()
    container.add_text_display("page")
    # history_len=1 -> no pagination, so send() returns right after the first respond.
    fake = _MultiPages({0: HMessage(components=[container])}, cv2=True, history_len=1)
    navigator = NavigatorView(pages=fake)  # ty: ignore[invalid-argument-type]

    ctx = _FakeCtx(fail_first=True)
    await navigator.send(ctx)  # ty: ignore[invalid-argument-type]

    assert len(ctx.calls) == 2  # failed initial render, then the fallback
    assert ctx.calls[1]["flags"] == h.MessageFlag.IS_COMPONENTS_V2


# --- per-instance button ids (cross-routing regression) --------------------------


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
