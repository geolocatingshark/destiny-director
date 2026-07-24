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

"""Unit tests for the hardened ``crosspost_message_with_retries``.

The web publish path passes ``max_attempts`` to opt into STRICT mode: a failed crosspost
must RAISE (so the form shows an error instead of the request hanging forever), and a
non-news channel must raise rather than be silently skipped (which used to leave the
caller marking the post "published" when nothing was broadcast). The automatic producers
keep the legacy fire-and-forget behaviour (``max_attempts is None``).
"""

import typing as t
from unittest.mock import MagicMock

import hikari as h
import pytest

from dd.anchor import utils as autils
from dd.anchor.utils import CrosspostError, crosspost_message_with_retries
from dd.common.bot import CachedFetchBot


class _FakeRest:
    """Records crosspost attempts; each attempt applies the next configured effect.

    ``effects[i]`` is ``None`` (success) or an ``Exception`` to raise; the last effect
    repeats once the list is exhausted, so ``[RuntimeError(...)]`` fails every time.
    """

    def __init__(self, effects: list[t.Any]) -> None:
        self.effects = effects
        self.calls = 0

    async def crosspost_message(self, channel_id: int, message_id: int) -> None:
        eff = self.effects[min(self.calls, len(self.effects) - 1)]
        self.calls += 1
        if isinstance(eff, Exception):
            raise eff


class _FakeBot:
    def __init__(self, effects: list[t.Any] | None = None) -> None:
        self.rest = _FakeRest(effects if effects is not None else [None])
        self.cache = MagicMock()


def _cb(fake: _FakeBot) -> CachedFetchBot:
    """Cast the recording fake to the ``CachedFetchBot`` the helper is typed for."""
    return t.cast(CachedFetchBot, fake)


def _news(cid: int = 100) -> t.Any:
    ch = MagicMock(spec=h.GuildNewsChannel)
    ch.id = cid
    return ch


def _text(cid: int = 100) -> t.Any:
    ch = MagicMock(spec=h.GuildTextChannel)
    ch.id = cid
    return ch


def _forbidden() -> h.ForbiddenError:
    # classify_error -> PERMANENT
    return h.ForbiddenError(url="https://discord/x", headers={}, raw_body="x")


def _already_crossposted() -> h.BadRequestError:
    return h.BadRequestError(
        url="https://discord/x",
        headers={},
        raw_body="x",
        message="This message has already been crossposted",
    )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Don't actually wait out the backoff sleeps during a retry test."""

    async def _fast_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(autils.aio, "sleep", _fast_sleep)


# --- strict mode (the web publish path) --------------------------------------


@pytest.mark.asyncio
async def test_strict_non_news_raises_and_never_crossposts() -> None:
    # The false-"published" bug guard: a non-news channel must raise so the caller never
    # stamps crossposted/published for a broadcast that never happened.
    bot = _FakeBot()
    with pytest.raises(CrosspostError):
        await crosspost_message_with_retries(_cb(bot), _text(), 5, max_attempts=4)
    assert bot.rest.calls == 0


@pytest.mark.asyncio
async def test_strict_permanent_error_raises_immediately() -> None:
    # A permanent error (403) is hopeless — raise on the first failure, no backoff loop.
    bot = _FakeBot(effects=[_forbidden()])
    with pytest.raises(h.ForbiddenError):
        await crosspost_message_with_retries(_cb(bot), _news(), 5, max_attempts=4)
    assert bot.rest.calls == 1


@pytest.mark.asyncio
async def test_strict_transient_error_retries_then_raises() -> None:
    # A persistent transient error retries up to the budget, then RAISES (never loops
    # forever) so the publish request can't hang.
    bot = _FakeBot(effects=[RuntimeError("blip")])
    with pytest.raises(RuntimeError):
        await crosspost_message_with_retries(_cb(bot), _news(), 5, max_attempts=4)
    assert bot.rest.calls == 4


@pytest.mark.asyncio
async def test_strict_transient_then_success() -> None:
    # A transient blip that clears within the budget succeeds (no raise).
    bot = _FakeBot(effects=[RuntimeError("blip"), None])
    await crosspost_message_with_retries(_cb(bot), _news(), 5, max_attempts=4)
    assert bot.rest.calls == 2


@pytest.mark.asyncio
async def test_strict_already_crossposted_is_success() -> None:
    bot = _FakeBot(effects=[_already_crossposted()])
    await crosspost_message_with_retries(_cb(bot), _news(), 5, max_attempts=4)
    assert bot.rest.calls == 1


@pytest.mark.asyncio
async def test_strict_success_first_try() -> None:
    bot = _FakeBot(effects=[None])
    await crosspost_message_with_retries(_cb(bot), _news(), 5, max_attempts=4)
    assert bot.rest.calls == 1


# --- legacy fire-and-forget mode (the automatic producers) -------------------


@pytest.mark.asyncio
async def test_legacy_non_news_skips_silently() -> None:
    # Unchanged behaviour: no max_attempts -> a non-news channel is skipped, not raised.
    bot = _FakeBot()
    out = await crosspost_message_with_retries(_cb(bot), _text(), 5)
    assert out is None and bot.rest.calls == 0


@pytest.mark.asyncio
async def test_legacy_already_crossposted_is_success() -> None:
    bot = _FakeBot(effects=[_already_crossposted()])
    await crosspost_message_with_retries(_cb(bot), _news(), 5)
    assert bot.rest.calls == 1
