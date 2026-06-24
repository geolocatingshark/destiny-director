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

"""Live integration tests for the beacon mirror pipeline.

These drive the real ``message_*_repeater_impl`` functions over Discord's REST API
against the dedicated ``dd-test-env`` guild — no gateway connection is used. The impls
do all their Discord work over REST (``channel.send`` / ``dest_msg.edit`` /
``dest_msg.delete``), so a ``hikari.RESTApp``-acquired client wrapped with a null cache
is enough to exercise them end-to-end.

Isolation per run: Discord forbids bot tokens from creating guilds (``POST /guilds`` →
20001 "Bots cannot use this endpoint"), so a brand-new guild per run isn't possible.
Instead the harness reuses the dedicated test guild (``cfg.test_env[0]``) and isolates
runs by channel: it prefix-sweeps any leftover ``test90931-*`` channels at setup,
creates the channels each test needs, and deletes them on teardown.

Opt-in: marked ``discord`` so the default suite (``make test``, ``-m "not discord"``)
never runs them; use ``make test-integration`` / ``make test-mirror-integration``. The
bot token is reused from ``DISCORD_TOKEN_BEACON`` and the guild from ``TEST_ENV`` (both
via existing config; the token is read with ``os.getenv`` so a missing one skips rather
than erroring). Offline CI and the sandbox skip these.

Note: Discord rate-limits channel create/delete, so this module is intentionally small
and shares one live client across its tests (module-scoped fixture).
"""

import contextlib
import os
import typing as t
from collections.abc import AsyncIterator, Awaitable, Callable

import hikari as h
import pytest
import pytest_asyncio

from dd.beacon.extensions import mirror
from dd.common import cfg, schemas
from dd.common.bot import CachedFetchBot
from dd.common.schemas import MirroredChannel, MirroredMessage

# Reuse existing config — no dedicated test env vars. The beacon token is read with
# os.getenv so a missing token skips (rather than raising at import); the test guild is
# TEST_ENV's first id.
_TOKEN = os.getenv("DISCORD_TOKEN_BEACON")
_GUILD = cfg.test_env[0] if cfg.test_env else None

# Distinctive prefix so the per-run sweep can never match a real channel.
_PREFIX = "test90931-"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.discord,
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not (_TOKEN and _GUILD),
        reason="DISCORD_TOKEN_BEACON and TEST_ENV must be set for live mirror "
        "integration tests",
    ),
]


class _NullCache:
    """A cache whose every lookup misses, so ``fetch_*`` falls through to REST.

    Stands in for the gateway-populated cache the impls would normally read; the
    setters are no-ops since there is no gateway state to keep coherent.
    """

    def get_guild_channel(self, _id: int) -> None:
        return None

    def get_message(self, _id: int) -> None:
        return None

    def get_guild(self, _id: int) -> None:
        return None

    def set_guild_channel(self, _channel: object) -> None:
        return None

    def set_message(self, _message: object) -> None:
        return None


class _RestBot:
    """The slice of the ``CachedFetchBot`` surface the mirror impls actually call.

    Backed by a REST client with no gateway/cache, so every fetch hits Discord.
    """

    def __init__(self, rest: h.api.RESTClient) -> None:
        self.rest = rest
        self.cache = _NullCache()

    async def fetch_channel(self, channel_id: int) -> h.PartialChannel:
        return await self.rest.fetch_channel(channel_id)

    async def fetch_message(
        self, channel: h.SnowflakeishOr[h.TextableChannel], message_id: int
    ) -> h.Message:
        return await self.rest.fetch_message(channel, message_id)


class _MirrorEnv(t.NamedTuple):
    rest: h.api.RESTClient
    bot: CachedFetchBot
    guild_id: int
    make_channel: Callable[[str], Awaitable[h.GuildTextChannel]]


async def _sweep_test_channels(rest: h.api.RESTClient, guild_id: int) -> None:
    """Delete every ``_PREFIX`` channel in the guild (leftovers from prior runs)."""
    for channel in await rest.fetch_guild_channels(guild_id):
        if channel.name and channel.name.startswith(_PREFIX):
            with contextlib.suppress(Exception):
                await rest.delete_channel(channel.id)


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def mirror_env() -> AsyncIterator[_MirrorEnv]:
    """A live REST client + channel factory against the dedicated test guild.

    Bots can't create guilds, so isolation is per-channel: a prefix sweep clears any
    leftovers at setup, and every channel created through ``make_channel`` is deleted on
    teardown — so a crashed run still cleans up after itself.
    """
    assert _TOKEN and _GUILD  # guaranteed by the module skipif
    guild_id = _GUILD

    rest_app = h.RESTApp()
    await rest_app.start()
    created: list[int] = []
    try:
        async with rest_app.acquire(_TOKEN, token_type="Bot") as rest:
            await _sweep_test_channels(rest, guild_id)

            async def make_channel(name: str) -> h.GuildTextChannel:
                channel = await rest.create_guild_text_channel(
                    guild_id, f"{_PREFIX}{name}"
                )
                created.append(channel.id)
                return channel

            try:
                yield _MirrorEnv(
                    rest=rest,
                    bot=t.cast(CachedFetchBot, _RestBot(rest)),
                    guild_id=guild_id,
                    make_channel=make_channel,
                )
            finally:
                for channel_id in created:
                    # Best-effort; the "break" test may already have deleted it.
                    with contextlib.suppress(Exception):
                        await rest.delete_channel(channel_id)
    finally:
        await rest_app.close()


@pytest.fixture(autouse=True)
def _silence_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop the impls posting a CV2 progress message to ``cfg.log_channel`` in tests."""

    async def _noop(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(mirror, "start_progress_logger", _noop)


@pytest_asyncio.fixture(loop_scope="module", autouse=True)
async def _fresh_db() -> AsyncIterator[None]:
    """Reset the (SQLite, from conftest) schema between tests for hermetic rows."""
    await schemas.destroy_all()
    await schemas.create_all()
    yield


async def _send(env: _MirrorEnv, src: h.GuildTextChannel, content: str) -> h.Message:
    """Post ``content`` in ``src`` and drive the send pipeline once."""
    posted = await env.rest.create_message(src.id, content)
    src_channel = t.cast(h.TextableChannel, await env.rest.fetch_channel(src.id))
    await mirror.message_create_repeater_impl(
        posted, env.bot, src_channel, wait_for_crosspost=False
    )
    return posted


async def test_send_update_delete_lifecycle(mirror_env: _MirrorEnv) -> None:
    """A message mirrored to two dests follows the source through edit and delete."""
    src = await mirror_env.make_channel("src-life")
    dests = [await mirror_env.make_channel(f"dst-life{i}") for i in range(2)]
    for dest in dests:
        await MirroredChannel.add_mirror(
            src.id, dest.id, dest_server_id=mirror_env.guild_id, legacy=True
        )

    # SEND: every dest receives the message and a pairing row is recorded.
    posted = await _send(mirror_env, src, "hello mirror")
    pairs = await MirroredMessage.get_dest_msgs_and_channels(posted.id)
    assert {ch for _msg, ch in pairs} == {dest.id for dest in dests}
    for dest_msg_id, ch_id in pairs:
        mirrored = await mirror_env.rest.fetch_message(ch_id, dest_msg_id)
        assert mirrored.content == "hello mirror"

    # UPDATE: an edit at the source propagates to every dest.
    await mirror_env.rest.edit_message(src.id, posted.id, "hello edited")
    edited = await mirror_env.rest.fetch_message(src.id, posted.id)
    await mirror.message_update_repeater_impl(edited, mirror_env.bot)
    for dest_msg_id, ch_id in pairs:
        mirrored = await mirror_env.rest.fetch_message(ch_id, dest_msg_id)
        assert mirrored.content == "hello edited"

    # DELETE: deleting the source removes every mirrored message.
    await mirror.message_delete_repeater_impl(posted.id, None, mirror_env.bot)
    for dest_msg_id, ch_id in pairs:
        with pytest.raises(h.NotFoundError):
            await mirror_env.rest.fetch_message(ch_id, dest_msg_id)


async def test_failing_dest_is_disabled(
    mirror_env: _MirrorEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken dest fails while a healthy one succeeds, and crosses the disable
    threshold so it is auto-disabled."""
    # Make retries instant and turn on auto-disable for this run.
    monkeypatch.setattr(mirror, "randint", lambda _a, _b: 0)
    monkeypatch.setattr(cfg, "disable_bad_channels", True)

    src = await mirror_env.make_channel("src-fail")
    good = await mirror_env.make_channel("dst-good")
    broken = await mirror_env.make_channel("dst-broken")
    for dest in (good, broken):
        await MirroredChannel.add_mirror(
            src.id, dest.id, dest_server_id=mirror_env.guild_id, legacy=True
        )

    # "Break" the dest: delete the channel but keep its (enabled) mirror row, so
    # the next send fails for it. Pre-load two failures so a single failing send
    # reaches the disable threshold of 3.
    await mirror_env.rest.delete_channel(broken.id)
    await MirroredChannel.log_legacy_mirror_failure_in_batch(src.id, [broken.id])
    await MirroredChannel.log_legacy_mirror_failure_in_batch(src.id, [broken.id])

    posted = await _send(mirror_env, src, "partial send")

    # The healthy dest still got the message...
    pairs = await MirroredMessage.get_dest_msgs_and_channels(posted.id)
    assert {ch for _msg, ch in pairs} == {good.id}
    mirrored = await mirror_env.rest.fetch_message(good.id, pairs[0][0])
    assert mirrored.content == "partial send"

    # ...and the broken dest's mirror row has been disabled.
    enabled_dests = await MirroredChannel.fetch_dests(src.id)
    assert good.id in enabled_dests
    assert broken.id not in enabled_dests


async def test_role_ping_is_appended(mirror_env: _MirrorEnv) -> None:
    """A dest configured with a role mention gets the spoilered ping suffix."""
    role_id = 123456789012345678
    src = await mirror_env.make_channel("src-ping")
    dest = await mirror_env.make_channel("dst-ping")
    await MirroredChannel.add_mirror(
        src.id,
        dest.id,
        dest_server_id=mirror_env.guild_id,
        legacy=True,
        role_mention_id=role_id,
    )

    posted = await _send(mirror_env, src, "ping me")
    pairs = await MirroredMessage.get_dest_msgs_and_channels(posted.id)
    (dest_msg_id, ch_id) = pairs[0]
    mirrored = await mirror_env.rest.fetch_message(ch_id, dest_msg_id)
    assert mirrored.content == f"ping me\n\n||<@&{role_id}>||"
