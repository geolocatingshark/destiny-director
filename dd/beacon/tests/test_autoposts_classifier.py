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

"""Tests for the autopost mirror-error classifier (synthetic hikari errors, no I/O)."""

import typing as t
from types import SimpleNamespace

import hikari as h

from dd.beacon.extensions.autoposts import (
    _OUTCOME_BY_CODE,
    MirrorOutcome,
    _supports_webhook_follow,
    classify_mirror_error,
)


def _hikari_error(
    cls: type[h.ForbiddenError] | type[h.BadRequestError] | type[h.NotFoundError],
    code: int,
) -> h.HTTPResponseError:
    return cls(url="https://x", headers={}, raw_body="", message="m", code=code)


def test_missing_permissions_codes_classify_as_missing_perms():
    assert (
        classify_mirror_error(_hikari_error(h.ForbiddenError, 50013))
        is MirrorOutcome.MISSING_PERMS
    )
    assert (
        classify_mirror_error(_hikari_error(h.ForbiddenError, 50001))
        is MirrorOutcome.MISSING_PERMS
    )


def test_cannot_execute_on_channel_type_classifies_as_needs_legacy():
    assert (
        classify_mirror_error(_hikari_error(h.BadRequestError, 50024))
        is MirrorOutcome.NEEDS_LEGACY
    )


def test_unknown_channel_classifies_as_channel_gone():
    assert (
        classify_mirror_error(_hikari_error(h.NotFoundError, 10003))
        is MirrorOutcome.CHANNEL_GONE
    )


def test_unmapped_code_and_non_hikari_classify_as_other():
    # A hikari error with an unmapped code falls through to OTHER (→ re-raise).
    assert (
        classify_mirror_error(_hikari_error(h.ForbiddenError, 50007))
        is MirrorOutcome.OTHER
    )
    # A non-hikari error has no .code → OTHER.
    assert classify_mirror_error(ValueError("boom")) is MirrorOutcome.OTHER


def test_bot_missing_send_is_named_but_not_a_discord_code():
    # The proactive gate's identity: an enum member that is deliberately *not* mapped
    # from any Discord error code (it's decided before any API call).
    assert isinstance(MirrorOutcome.BOT_MISSING_SEND, MirrorOutcome)
    assert MirrorOutcome.BOT_MISSING_SEND not in _OUTCOME_BY_CODE.values()


def _stub_channel(channel_type: h.ChannelType) -> h.PartialChannel:
    return t.cast(h.PartialChannel, SimpleNamespace(type=channel_type))


def test_supports_webhook_follow_only_for_text_channels():
    assert _supports_webhook_follow(_stub_channel(h.ChannelType.GUILD_TEXT)) is True
    for other in (
        h.ChannelType.GUILD_PUBLIC_THREAD,
        h.ChannelType.GUILD_PRIVATE_THREAD,
        h.ChannelType.GUILD_NEWS_THREAD,
        h.ChannelType.GUILD_FORUM,
        h.ChannelType.GUILD_MEDIA,
        h.ChannelType.GUILD_VOICE,
        h.ChannelType.GUILD_STAGE,
        h.ChannelType.GUILD_NEWS,
    ):
        assert _supports_webhook_follow(_stub_channel(other)) is False
