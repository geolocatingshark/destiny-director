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
    def __init__(self, *, edit_raises: BaseException | None = None) -> None:
        self._initial = object()
        self._edit_raises = edit_raises
        self.edit_message_calls: list[tuple[t.Any, t.Any]] = []

    async def fetch_initial_response(self) -> object:
        return self._initial

    async def edit_message(self, message: t.Any, *, components: t.Any) -> None:
        self.edit_message_calls.append((message, components))
        if self._edit_raises is not None:
            raise self._edit_raises


class _FakeCtx:
    """Minimal ``lb.Context`` stand-in covering only what ``Paginator.send`` uses."""

    def __init__(self, *, edit_raises: BaseException | None = None) -> None:
        self.interaction = _FakeInteraction(edit_raises=edit_raises)
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


async def test_on_timeout_swallows_expired_interaction_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired interaction token during the disable-edit must not surface.

    Regression: ``navigator_timeout`` equalled the 15-minute interaction-token lifetime,
    so the on-timeout edit raced token expiry and 401'd ("Invalid Webhook Token"),
    propagating out of ``send`` as a phantom ``/help`` failure. ``_on_timeout`` is
    best-effort and must swallow that ``UnauthorizedError``.
    """
    paginator = _two_page_paginator()

    async def _attach_times_out(self: t.Any, client: t.Any, *, timeout: t.Any) -> None:
        raise TimeoutError

    monkeypatch.setattr(lbc.Menu, "attach", _attach_times_out)

    expired = h.UnauthorizedError(
        url="https://discord.com/api/v10/webhooks/x/y/messages/z",
        headers={},
        raw_body="",
        message="Invalid Webhook Token",
        code=50027,
    )
    ctx = _FakeCtx(edit_raises=expired)
    # Must not raise — the disable-edit was attempted but the token had expired.
    await paginator.send(t.cast(t.Any, ctx))

    assert len(ctx.interaction.edit_message_calls) == 1


async def test_timeout_is_capped_below_interaction_token_lifetime() -> None:
    """A timeout at/above the token lifetime is clamped so the edit can still land."""
    paginator = components.Paginator(
        [_container_page("a"), _container_page("b")], timeout=900
    )
    assert paginator._timeout == components._MAX_TIMEOUT
    assert paginator._timeout < components._INTERACTION_TOKEN_TTL

    # A short, safe timeout is left untouched.
    short = components.Paginator(
        [_container_page("a"), _container_page("b")], timeout=30
    )
    assert short._timeout == 30


def _collect_custom_ids(comps: t.Iterable[t.Any]) -> list[str]:
    out: list[str] = []
    for c in comps:
        cid = getattr(c, "custom_id", None)
        if cid is not None:
            out.append(cid)
        children = getattr(c, "components", None)
        if children:
            out.extend(_collect_custom_ids(children))
    return out


async def test_paginators_use_distinct_instance_button_ids() -> None:
    # Regression: shared button custom_ids let a press on one paginator's message be
    # routed by lightbulb to another live paginator's menu. Per-instance ids keep a
    # press routing only to the paginator that owns the pressed message.
    p1 = _two_page_paginator()
    p2 = _two_page_paginator()

    assert p1._prev_id != p2._prev_id
    assert p1._next_id != p2._next_id

    # The rendered CV2 nav row carries this instance's ids (matching its own menu).
    ids = _collect_custom_ids(p1._render_components())
    assert p1._prev_id in ids
    assert p1._next_id in ids
    assert p2._prev_id not in ids
