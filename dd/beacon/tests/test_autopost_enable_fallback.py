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

"""_enable_autopost's follow → legacy degradation (finding #2). The follow-webhook path
is stubbed to raise, so no Discord I/O happens."""

from unittest.mock import AsyncMock, MagicMock

import hikari as h
import pytest

from dd.beacon.extensions import autoposts

pytestmark = pytest.mark.asyncio


def _forbidden(code: int) -> h.ForbiddenError:
    return h.ForbiddenError(
        url="https://x", headers={}, raw_body="", message="m", code=code
    )


def _text_target() -> MagicMock:
    target = MagicMock(spec=h.GuildTextChannel)
    target.type = h.ChannelType.GUILD_TEXT  # so _supports_webhook_follow is True
    return target


async def _run(monkeypatch, non_legacy_exc: BaseException) -> AsyncMock:
    """Drive _enable_autopost with the follow path raising ``non_legacy_exc``; return
    the (stubbed) legacy-mirror mock so callers can assert whether it fired."""
    non_legacy = AsyncMock(side_effect=non_legacy_exc)
    legacy = AsyncMock()
    monkeypatch.setattr(autoposts, "enable_non_legacy_mirror", non_legacy)
    monkeypatch.setattr(autoposts, "enable_legacy_mirror", legacy)
    await autoposts._enable_autopost(
        bot=MagicMock(),
        followable_channel=123,
        ctx=MagicMock(),
        ping_role=None,
        session=MagicMock(),
        target_channel=_text_target(),
    )
    non_legacy.assert_awaited_once()
    return legacy


async def test_missing_webhooks_403_propagates(monkeypatch) -> None:
    # Manage Webhooks is a hard requirement — Preflight 3 blocks a missing-webhooks
    # enable before the follow path runs, so a MISSING_PERMS 403 that still reaches the
    # follow path is unexpected and must surface (via the reactive handler), NOT
    # silently degrade to a legacy mirror.
    non_legacy = AsyncMock(side_effect=_forbidden(50013))
    legacy = AsyncMock()
    monkeypatch.setattr(autoposts, "enable_non_legacy_mirror", non_legacy)
    monkeypatch.setattr(autoposts, "enable_legacy_mirror", legacy)
    with pytest.raises(h.ForbiddenError):
        await autoposts._enable_autopost(
            bot=MagicMock(),
            followable_channel=123,
            ctx=MagicMock(),
            ping_role=None,
            session=MagicMock(),
            target_channel=_text_target(),
        )
    legacy.assert_not_awaited()


async def test_needs_legacy_400_degrades_to_legacy(monkeypatch) -> None:
    # 50024 (announce channel) still degrades, as before.
    bad_request = h.BadRequestError(
        url="https://x", headers={}, raw_body="", message="m", code=50024
    )
    legacy = await _run(monkeypatch, bad_request)
    legacy.assert_awaited_once()


async def test_unrelated_forbidden_propagates(monkeypatch) -> None:
    # A ForbiddenError with an unmapped code classifies as OTHER — a real error that
    # must NOT be masked by a silent legacy fallback.
    non_legacy = AsyncMock(side_effect=_forbidden(99999))
    legacy = AsyncMock()
    monkeypatch.setattr(autoposts, "enable_non_legacy_mirror", non_legacy)
    monkeypatch.setattr(autoposts, "enable_legacy_mirror", legacy)
    with pytest.raises(h.ForbiddenError):
        await autoposts._enable_autopost(
            bot=MagicMock(),
            followable_channel=123,
            ctx=MagicMock(),
            ping_role=None,
            session=MagicMock(),
            target_channel=_text_target(),
        )
    legacy.assert_not_awaited()
