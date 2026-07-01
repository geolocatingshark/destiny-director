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

"""Pure unit tests for the autopost permission diagnostics (no DB / network).

Covers the required-vs-advisory perms table (``for_channel``), the best-effort
block-source attribution (``utils.explain_missing_permission``), and the rendered
diagnostics embed (``permission_error_embed``)."""

import typing as t
from types import SimpleNamespace
from unittest.mock import MagicMock

import hikari as h

from dd.beacon import utils
from dd.beacon.extensions.autoposts import (
    AutopostPerm,
    PermStatus,
    for_channel,
    permission_error_embed,
)

# --- perms table -----------------------------------------------------------------


def test_for_channel_required_and_advisory_base() -> None:
    perms = for_channel(MagicMock(spec=h.GuildTextChannel))
    required = {p.label for p in perms if p.required}
    advisory = {p.label for p in perms if not p.required}
    assert required == {"View Channel", "Send Messages"}
    assert advisory == {"Embed Links", "Manage Webhooks"}


def test_for_channel_thread_adds_send_in_threads() -> None:
    perms = for_channel(MagicMock(spec=h.GuildThreadChannel))
    sit = next(p for p in perms if p.label == "Send Messages in Threads")
    assert sit.required is True
    assert sit.permission == h.Permissions.SEND_MESSAGES_IN_THREADS


# --- explain_missing_permission --------------------------------------------------

GUILD_ID = 1  # the @everyone role id equals the guild id
MEMBER_ID = 100
ROLE_A = 10


def _ow(
    target_id: int,
    *,
    allow: h.Permissions = h.Permissions.NONE,
    deny: h.Permissions = h.Permissions.NONE,
) -> h.PermissionOverwrite:
    return h.PermissionOverwrite(
        id=h.Snowflake(target_id),
        type=h.PermissionOverwriteType.ROLE,
        allow=allow,
        deny=deny,
    )


def _role(
    rid: int, name: str, permissions: h.Permissions = h.Permissions.NONE
) -> SimpleNamespace:
    return SimpleNamespace(id=rid, name=name, permissions=permissions)


def _guild(roles: list[SimpleNamespace], owner_id: int = 999) -> SimpleNamespace:
    return SimpleNamespace(
        id=GUILD_ID,
        owner_id=owner_id,
        get_roles=lambda: {r.id: r for r in roles},
    )


def _member(role_ids: list[int], guild: SimpleNamespace) -> h.Member:
    return t.cast(
        h.Member,
        SimpleNamespace(id=MEMBER_ID, role_ids=role_ids, get_guild=lambda: guild),
    )


def _channel(overwrites: list[h.PermissionOverwrite]) -> h.PermissibleGuildChannel:
    return t.cast(
        h.PermissibleGuildChannel,
        SimpleNamespace(
            guild_id=GUILD_ID,
            permission_overwrites={ow.id: ow for ow in overwrites},
        ),
    )


def test_member_overwrite_deny_is_most_specific() -> None:
    everyone = _role(
        GUILD_ID, "everyone", h.Permissions.VIEW_CHANNEL | h.Permissions.SEND_MESSAGES
    )
    member = _member([], _guild([everyone]))
    channel = _channel([_ow(MEMBER_ID, deny=h.Permissions.SEND_MESSAGES)])
    result = utils.explain_missing_permission(
        member, channel, h.Permissions.SEND_MESSAGES
    )
    assert result == "a channel permission override on me denies it"


def test_role_overwrite_deny_names_the_role() -> None:
    everyone = _role(GUILD_ID, "everyone", h.Permissions.SEND_MESSAGES)
    mods = _role(ROLE_A, "Mods")
    member = _member([ROLE_A], _guild([everyone, mods]))
    channel = _channel([_ow(ROLE_A, deny=h.Permissions.SEND_MESSAGES)])
    result = utils.explain_missing_permission(
        member, channel, h.Permissions.SEND_MESSAGES
    )
    assert result == "a channel override on the @Mods role denies it"


def test_everyone_overwrite_deny() -> None:
    everyone = _role(GUILD_ID, "everyone", h.Permissions.SEND_MESSAGES)
    member = _member([], _guild([everyone]))
    channel = _channel([_ow(GUILD_ID, deny=h.Permissions.SEND_MESSAGES)])
    result = utils.explain_missing_permission(
        member, channel, h.Permissions.SEND_MESSAGES
    )
    assert result == "the channel's @everyone override denies it"


def test_not_granted_at_guild_level() -> None:
    everyone = _role(GUILD_ID, "everyone", h.Permissions.VIEW_CHANNEL)
    member = _member([], _guild([everyone]))
    result = utils.explain_missing_permission(
        member, _channel([]), h.Permissions.SEND_MESSAGES
    )
    assert result is not None
    assert "none of my roles grant it here" in result


def test_present_permission_returns_none() -> None:
    everyone = _role(GUILD_ID, "everyone", h.Permissions.SEND_MESSAGES)
    member = _member([], _guild([everyone]))
    assert (
        utils.explain_missing_permission(
            member, _channel([]), h.Permissions.SEND_MESSAGES
        )
        is None
    )


def test_guild_owner_returns_none() -> None:
    everyone = _role(GUILD_ID, "everyone")
    member = _member([], _guild([everyone], owner_id=MEMBER_ID))
    assert (
        utils.explain_missing_permission(
            member, _channel([]), h.Permissions.SEND_MESSAGES
        )
        is None
    )


# --- permission_error_embed ------------------------------------------------------


def _owner(username: str) -> h.User:
    return t.cast(
        h.User,
        SimpleNamespace(
            username=username, display_avatar_url="https://example.com/a.png"
        ),
    )


def test_permission_error_embed_renders_checklist() -> None:
    view = AutopostPerm(h.Permissions.VIEW_CHANNEL, "View Channel", True, "why-v")
    send = AutopostPerm(h.Permissions.SEND_MESSAGES, "Send Messages", True, "why-s")
    embed_links = AutopostPerm(h.Permissions.EMBED_LINKS, "Embed Links", False, "why-e")
    statuses = [
        PermStatus(view, True, None),
        PermStatus(send, False, "the channel's @everyone override denies it"),
        PermStatus(embed_links, False, None),
    ]
    embed = permission_error_embed([_owner("dd")], statuses, perms_known=True)

    assert embed.title == "Permission Error"
    desc = embed.description or ""
    assert "✅ View Channel" in desc
    assert "❌ Send Messages" in desc
    assert "    └ the channel's @everyone override denies it" in desc
    # Advisory perms are shown as "(recommended)" and never get a block line.
    assert "❌ Embed Links (recommended)" in desc
    assert "why-e" not in desc
    assert embed.footer is not None
    assert embed.footer.text == "@dd"


def test_permission_error_embed_unknown_perms_adds_note_and_static_why() -> None:
    send = AutopostPerm(h.Permissions.SEND_MESSAGES, "Send Messages", True, "why-s")
    embed = permission_error_embed(
        [_owner("dd")], [PermStatus(send, False, None)], perms_known=False
    )
    desc = embed.description or ""
    assert "couldn't read my own permissions" in desc
    # With no specific block source, the required perm falls back to its static why.
    assert "    └ why-s" in desc
