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

import hikari as h

from dd.beacon.extensions.autoposts import MirrorOutcome, classify_mirror_error


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
