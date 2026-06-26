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

# Tests for dd.common.components.Paginator — no real Discord I/O; small fakes stand in
# for the lightbulb context/interaction. Focuses on the timeout path: lightbulb's
# ``Menu.attach`` *raises* ``asyncio.TimeoutError`` when the menu times out, so the
# paginator must swallow it (a timed-out paginator is normal, not a command failure)
# and still disable its controls.

import typing as t

import hikari as h
import pytest
from lightbulb import components as lbc

from dd.common import components

pytestmark = pytest.mark.asyncio


def _container_page(text: str) -> components.Cv2PageFactory:
    """A CV2 page factory rendering a single container — mirrors the help factories."""

    def factory() -> list[h.api.ComponentBuilder]:
        return [components.build_container([text])]

    return factory


class _FakeInteraction:
    def __init__(self) -> None:
        self._initial = object()
        self.edit_message_calls: list[tuple[t.Any, t.Any]] = []

    async def fetch_initial_response(self) -> object:
        return self._initial

    async def edit_message(self, message: t.Any, *, components: t.Any) -> None:
        self.edit_message_calls.append((message, components))


class _FakeCtx:
    """Minimal ``lb.Context`` stand-in covering only what ``Paginator.send`` uses."""

    def __init__(self) -> None:
        self.interaction = _FakeInteraction()
        self.client = object()
        self.responses: list[dict[str, t.Any]] = []

    async def respond(self, **kwargs: t.Any) -> None:
        self.responses.append(kwargs)


def _two_page_paginator() -> components.Paginator:
    return components.Paginator(
        [_container_page("page one"), _container_page("page two")], timeout=1
    )


async def test_send_swallows_menu_timeout_and_disables_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timed-out paginator must not surface ``attach``'s ``TimeoutError``.

    Regression: ``Menu.attach`` raises ``asyncio.TimeoutError`` on timeout (it does not
    return), which previously propagated through ``send`` → the ``/help`` invoke and was
    logged as a phantom command failure. ``send`` must catch it and run the on-timeout
    cleanup so the controls are disabled.
    """
    paginator = _two_page_paginator()

    async def _attach_times_out(self: t.Any, client: t.Any, *, timeout: t.Any) -> None:
        raise TimeoutError

    monkeypatch.setattr(lbc.Menu, "attach", _attach_times_out)

    ctx = _FakeCtx()
    # Must not raise.
    await paginator.send(t.cast(t.Any, ctx))

    # The on-timeout cleanup ran: the message was edited once to disable the controls.
    assert len(ctx.interaction.edit_message_calls) == 1
    _, disabled_components = ctx.interaction.edit_message_calls[0]
    assert disabled_components  # re-rendered with all_disabled=True, not empty


async def test_send_single_page_attaches_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single-page paginator sends once and never attaches a menu (no controls)."""
    paginator = components.Paginator([_container_page("only page")], timeout=1)

    async def _attach_should_not_run(
        self: t.Any, client: t.Any, *, timeout: t.Any
    ) -> None:
        raise AssertionError("attach must not be called for a single page")

    monkeypatch.setattr(lbc.Menu, "attach", _attach_should_not_run)

    ctx = _FakeCtx()
    await paginator.send(t.cast(t.Any, ctx))

    assert len(ctx.responses) == 1
    assert ctx.interaction.edit_message_calls == []
