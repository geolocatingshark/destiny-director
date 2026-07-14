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


def _thread_target() -> MagicMock:
    target = MagicMock(spec=h.GuildThreadChannel)
    target.type = h.ChannelType.GUILD_PUBLIC_THREAD  # legacy-only (no webhook-follow)
    return target


async def test_ping_role_drops_follow_before_writing_legacy(monkeypatch) -> None:
    # The ping-role path must drop any prior follow webhook BEFORE recording the legacy
    # mirror, so a perms failure on the drop can't leave a half-applied row committed
    # (review finding #4). Assert the ordering by recording call order.
    order: list[str] = []

    async def fake_drop(*_a, **_k) -> None:
        order.append("drop")

    async def fake_legacy(*_a, **_k) -> None:
        order.append("legacy")

    monkeypatch.setattr(autoposts, "_drop_existing_follow", fake_drop)
    monkeypatch.setattr(autoposts, "enable_legacy_mirror", fake_legacy)
    await autoposts._enable_autopost(
        bot=MagicMock(),
        followable_channel=123,
        ctx=MagicMock(),
        ping_role=MagicMock(spec=h.Role),
        session=MagicMock(),
        target_channel=_text_target(),
    )
    assert order == ["drop", "legacy"]


async def test_ping_role_thread_skips_follow_drop(monkeypatch) -> None:
    # A thread never had a follow webhook (legacy-only), so the ping-role path must not
    # attempt the (Manage-Webhooks-gated) drop — that would false-block a valid enable.
    drop = AsyncMock()
    legacy = AsyncMock()
    monkeypatch.setattr(autoposts, "_drop_existing_follow", drop)
    monkeypatch.setattr(autoposts, "enable_legacy_mirror", legacy)
    await autoposts._enable_autopost(
        bot=MagicMock(),
        followable_channel=123,
        ctx=MagicMock(),
        ping_role=MagicMock(spec=h.Role),
        session=MagicMock(),
        target_channel=_thread_target(),
    )
    drop.assert_not_awaited()
    legacy.assert_awaited_once()


async def _run_disable(monkeypatch, unfollow_exc: BaseException | None) -> AsyncMock:
    """Drive disable_mirror for a follow-webhook mirror; return the remove mock."""
    ctx = MagicMock()
    ctx.channel_id = 999
    # fetch_dests returns legacy dests only; 999 isn't among them → follow-webhook path.
    monkeypatch.setattr(
        autoposts.MirroredChannel, "fetch_dests", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        autoposts,
        "unfollow_channel",
        AsyncMock(side_effect=unfollow_exc) if unfollow_exc else AsyncMock(),
    )
    remove = AsyncMock()
    monkeypatch.setattr(autoposts.MirroredChannel, "remove_mirror", remove)
    monkeypatch.setattr(autoposts.mirror_tracing, "forget_traced_mirror", MagicMock())
    await autoposts.disable_mirror(
        ctx=ctx, followable_channel=123, bot=MagicMock(), session=MagicMock()
    )
    return remove


async def test_disable_follow_mirror_removes_record_without_manage_webhooks(
    monkeypatch,
) -> None:
    # The webhook delete 403s (bot lost Manage Webhooks), but the mirror record must
    # STILL be removed — never gate disable (review finding #2).
    remove = await _run_disable(monkeypatch, _forbidden(50013))
    remove.assert_awaited_once()


async def test_disable_follow_mirror_propagates_non_perms_error(monkeypatch) -> None:
    # A non-perms error during unfollow is real and must surface, NOT be swallowed.
    ctx = MagicMock()
    ctx.channel_id = 999
    monkeypatch.setattr(
        autoposts.MirroredChannel, "fetch_dests", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        autoposts, "unfollow_channel", AsyncMock(side_effect=_forbidden(99999))
    )
    remove = AsyncMock()
    monkeypatch.setattr(autoposts.MirroredChannel, "remove_mirror", remove)
    monkeypatch.setattr(autoposts.mirror_tracing, "forget_traced_mirror", MagicMock())
    with pytest.raises(h.ForbiddenError):
        await autoposts.disable_mirror(
            ctx=ctx, followable_channel=123, bot=MagicMock(), session=MagicMock()
        )
    remove.assert_not_awaited()
