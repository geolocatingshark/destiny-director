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

"""Guards the early enabled-check in ``api_to_discord_announcer``.

A disabled (or never-enabled) autopost must bail out *before* posting the
"Waiting for data…" placeholder — otherwise it leaks an orphan message that is
never edited or cleaned up. See plans/announcer_disabled_placeholder_leak.md.
"""

import typing as t

import pytest

from dd.anchor.extensions import xur
from dd.common.bot import CachedFetchBot
from dd.hmessage import HMessage


async def _fail_construct(*args, **kwargs):  # pragma: no cover - must never run
    raise AssertionError("construct_message_coro should not run when disabled")


class _FakeMessage:
    """Stands in for the posted placeholder message (``.id`` + awaitable ``.edit``)."""

    def __init__(self):
        self.id = 999
        self.edits: list[dict] = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled_return", [False, None])
async def test_disabled_autopost_posts_nothing(monkeypatch, enabled_return):
    calls = []

    async def fake_send_message(*args, **kwargs):
        calls.append((args, kwargs))

    async def enabled_check_coro():
        return enabled_return

    monkeypatch.setattr(xur.utils, "send_message", fake_send_message)

    result = await xur.api_to_discord_announcer(
        bot=t.cast(CachedFetchBot, object()),
        channel_id=123,
        construct_message_coro=_fail_construct,
        check_enabled=True,
        enabled_check_coro=enabled_check_coro,
    )

    assert result is None
    assert calls == []  # placeholder never posted


@pytest.mark.asyncio
async def test_no_enabled_coro_with_check_bails(monkeypatch):
    calls = []

    async def fake_send_message(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(xur.utils, "send_message", fake_send_message)

    result = await xur.api_to_discord_announcer(
        bot=t.cast(CachedFetchBot, object()),
        channel_id=123,
        construct_message_coro=_fail_construct,
        check_enabled=True,
        enabled_check_coro=None,
    )

    assert result is None
    assert calls == []


def _patch_happy_path(monkeypatch, msg):
    """Stub the announcer's I/O so it can run end-to-end in-process."""
    crossposts = []

    async def fake_send_message(*args, **kwargs):
        return msg

    async def fake_online(*args, **kwargs):
        return True

    async def fake_crosspost(bot, channel_id, message_id):
        crossposts.append(message_id)

    monkeypatch.setattr(xur.utils, "send_message", fake_send_message)
    monkeypatch.setattr(xur.api, "check_bungie_api_online", fake_online)
    monkeypatch.setattr(xur.utils, "crosspost_message_with_retries", fake_crosspost)
    # Skip the 5s pre-crosspost sleep (and any backoff) so the test is instant.
    monkeypatch.setattr(xur.aio, "sleep", lambda *a, **k: _noop())
    return crossposts


async def _noop():
    return None


@pytest.mark.asyncio
async def test_enabled_autopost_posts_edits_and_crossposts(monkeypatch):
    msg = _FakeMessage()
    crossposts = _patch_happy_path(monkeypatch, msg)
    real = HMessage(components=[])

    async def construct(bot):
        return real

    async def enabled():
        return True

    await xur.api_to_discord_announcer(
        bot=t.cast(CachedFetchBot, object()),
        channel_id=123,
        construct_message_coro=construct,
        check_enabled=True,
        enabled_check_coro=enabled,
        cv2=True,
    )

    assert msg.edits, "placeholder must be edited to the real content"
    assert crossposts == [msg.id]


@pytest.mark.asyncio
async def test_toggle_off_mid_run_still_completes(monkeypatch):
    """Once the placeholder is up, a later disable must NOT orphan it.

    The enabled coro returns True on the first (pre-placeholder) call and False
    afterwards. With the in-loop rechecks removed, the announcer still edits the
    placeholder to real content rather than returning and leaving it stranded.
    """
    msg = _FakeMessage()
    _patch_happy_path(monkeypatch, msg)
    real = HMessage(components=[])
    seen = {"n": 0}

    async def construct(bot):
        return real

    async def enabled():
        seen["n"] += 1
        return seen["n"] == 1  # True only for the initial gate

    await xur.api_to_discord_announcer(
        bot=t.cast(CachedFetchBot, object()),
        channel_id=123,
        construct_message_coro=construct,
        check_enabled=True,
        enabled_check_coro=enabled,
        cv2=True,
    )

    assert seen["n"] == 1, "enabled coro must only be consulted once, before posting"
    assert msg.edits, "placeholder must still be edited despite the mid-run disable"
