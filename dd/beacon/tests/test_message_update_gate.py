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

"""Unit tests for the message-update content-edit gate (no DB / network / Discord).

Discord fires a MessageUpdateEvent for publishes/crossposts, embed unfurls and flag
changes as well as genuine content edits. Only a real edit sets ``edited_timestamp``;
``is_content_edit`` is what keeps the automatic mirror reconcile from racing an
in-flight create on a publish event.
"""

import datetime as dt
import typing as t
from types import SimpleNamespace

import hikari as h

from dd.beacon.extensions.mirror import is_content_edit


def _message(edited_timestamp: object) -> h.PartialMessage:
    # A stand-in carrying only the attribute the gate reads.
    return t.cast(h.PartialMessage, SimpleNamespace(edited_timestamp=edited_timestamp))


def test_real_content_edit_has_a_timestamp() -> None:
    edited = dt.datetime(2026, 6, 24, tzinfo=dt.UTC)
    assert is_content_edit(_message(edited)) is True


def test_publish_or_flag_change_leaves_timestamp_none() -> None:
    # A publish/crosspost or flag change reports edited_timestamp as None.
    assert is_content_edit(_message(None)) is False


def test_field_absent_from_partial_payload_is_undefined() -> None:
    # An embed unfurl / partial payload may omit the field entirely (UNDEFINED).
    assert is_content_edit(_message(h.UNDEFINED)) is False
