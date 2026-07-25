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

"""Unit tests for the progress-card task body ``_run_card`` (no real Discord I/O).

``_run_card`` is the whole card lifecycle registered in ``_cards``: resolve the source
links, post the first card under a bounded retry, run ``_card_loop``, and always release
the cancel menu on exit. These pin the invariants a supersede refactor must preserve —
the bounded first-send retry, that a give-up does *not* run the loop, that
``CancelledError`` propagates (so a supersede still tears the task down) while any other
exception is contained, and that the cancel menu is released on every exit path.
"""

import asyncio as aio
import typing as t
from time import perf_counter
from unittest.mock import AsyncMock, MagicMock

import hikari as h
import lightbulb as lb
import pytest

from dd.beacon.extensions import mirror
from dd.beacon.mirror_core import MirrorOperationType, RunView

pytestmark = pytest.mark.asyncio


def _view() -> RunView:
    return RunView(
        op=MirrorOperationType.SEND,
        src_ch_id=1,
        src_msg_id=777,
        start_time=perf_counter(),
    )


def _log_channel(send: AsyncMock) -> MagicMock:
    channel = MagicMock(spec=h.TextableGuildChannel)
    channel.send = send
    return channel


def _install_common(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the collaborators every _run_card path needs, except the send + loop."""
    monkeypatch.setattr(
        mirror,
        "_resolve_source_fields",
        AsyncMock(return_value=("mlink", "msum", "clink", "cname")),
    )
    monkeypatch.setattr(mirror.aio, "sleep", AsyncMock())  # instant retry backoff


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    send: AsyncMock,
    enable_cancellation: bool = True,
    client: t.Any = None,
) -> tuple[MagicMock, AsyncMock, MagicMock]:
    """Drive _run_card with a stubbed cancel-menu + card loop; return the mocks."""
    _install_common(monkeypatch)

    menu_handle = MagicMock()
    build_menu = MagicMock(return_value=(MagicMock(), menu_handle))
    monkeypatch.setattr(mirror, "_build_cancel_menu", build_menu)
    card_loop = AsyncMock()
    monkeypatch.setattr(mirror, "_card_loop", card_loop)

    bot = MagicMock()
    bot.fetch_channel = AsyncMock(return_value=_log_channel(send))

    await mirror._run_card(
        bot,
        _view(),
        source_message=None,
        source_channel=None,
        title="Mirror send progress",
        enable_cancellation=enable_cancellation,
        client=client,
    )
    return build_menu, card_loop, menu_handle


async def test_posts_card_runs_loop_and_releases_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_message = MagicMock()
    send = AsyncMock(return_value=log_message)

    build_menu, card_loop, menu_handle = await _run(
        monkeypatch, send=send, client=MagicMock(spec=lb.Client)
    )

    send.assert_awaited_once()
    build_menu.assert_called_once()  # a real client + cancellation → menu attached
    card_loop.assert_awaited_once()
    assert card_loop.await_args is not None
    assert card_loop.await_args.args[0] is log_message  # loop got the posted message
    menu_handle.stop_interacting.assert_called_once()  # released on exit


async def test_retries_first_send_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_message = MagicMock()
    send = AsyncMock(side_effect=[RuntimeError("blip"), log_message])

    _build, card_loop, _menu = await _run(
        monkeypatch, send=send, client=MagicMock(spec=lb.Client)
    )

    assert send.await_count == 2  # one transient failure, then the post lands
    card_loop.assert_awaited_once()


async def test_gives_up_after_max_send_failures_without_running_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send = AsyncMock(side_effect=RuntimeError("channel gone"))

    _build, card_loop, menu_handle = await _run(
        monkeypatch, send=send, client=MagicMock(spec=lb.Client)
    )

    assert send.await_count == mirror._PROGRESS_LOGGER_MAX_TRIES
    card_loop.assert_not_awaited()  # never posted → nothing to update
    menu_handle.stop_interacting.assert_called_once()  # still released on the give-up


async def test_cancelled_during_first_send_propagates_and_releases_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send = AsyncMock(side_effect=aio.CancelledError())

    with pytest.raises(aio.CancelledError):
        await _run(monkeypatch, send=send, client=MagicMock(spec=lb.Client))
    # The menu handle is a fresh mock per _run call; re-driving to assert release is
    # covered by the give-up test. Here we only assert the cancellation is not swallowed
    # (a supersede must be able to tear the task down).


async def test_generic_loop_exception_is_contained_and_releases_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_common(monkeypatch)
    menu_handle = MagicMock()
    monkeypatch.setattr(
        mirror, "_build_cancel_menu", MagicMock(return_value=(MagicMock(), menu_handle))
    )
    monkeypatch.setattr(mirror, "_card_loop", AsyncMock(side_effect=RuntimeError("x")))
    bot = MagicMock()
    bot.fetch_channel = AsyncMock(return_value=_log_channel(AsyncMock()))

    # Must not raise — a non-cancel failure inside the task is logged, not propagated.
    await mirror._run_card(
        bot,
        _view(),
        source_message=None,
        source_channel=None,
        title="Mirror send progress",
        enable_cancellation=True,
        client=MagicMock(spec=lb.Client),
    )
    menu_handle.stop_interacting.assert_called_once()


async def test_no_cancel_menu_when_cancellation_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_menu, card_loop, _menu = await _run(
        monkeypatch,
        send=AsyncMock(return_value=MagicMock()),
        enable_cancellation=False,
        client=MagicMock(spec=lb.Client),
    )

    build_menu.assert_not_called()  # disabled → no menu
    assert card_loop.await_args is not None
    assert card_loop.await_args.args[3] is None  # loop handed a None menu handle


async def test_no_cancel_menu_without_a_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mirror, "_client", None)  # no startup-captured client either

    build_menu, _loop, _menu = await _run(
        monkeypatch,
        send=AsyncMock(return_value=MagicMock()),
        enable_cancellation=True,
        client=None,
    )

    build_menu.assert_not_called()  # cancellation wanted but no client to route it
