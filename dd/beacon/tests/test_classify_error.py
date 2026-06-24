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

"""Unit tests for :func:`dd.common.utils.classify_error` and friends.

Pure logic, no DB / live bot needed.
"""

import hikari as h
import pytest
from hikari.internal import routes

from dd.common.utils import (
    ErrorClass,
    classify_error,
    identity_for_exc,
    reference_code,
)


def _rate_limit_too_long() -> h.RateLimitTooLongError:
    """Build a 429 ``RateLimitTooLongError`` (its constructor needs a real route)."""
    route = routes.Route("POST", "/channels/{channel}/messages").compile(channel=1)
    return h.RateLimitTooLongError(
        route=route,
        is_global=True,
        retry_after=5.0,
        max_retry_after=2.0,
        reset_at=0.0,
        limit=None,
        period=None,
    )


def _http(
    cls: type[h.ForbiddenError] | type[h.BadRequestError] | type[h.NotFoundError],
    code: int = 0,
    message: str = "m",
) -> h.HTTPResponseError:
    """Build a hikari client error whose HTTP status is baked into the subclass."""
    return cls(url="https://x", headers={}, raw_body="", message=message, code=code)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        # 403 Missing Access / Missing Permissions -> permanent.
        (_http(h.ForbiddenError, code=50001), ErrorClass.PERMANENT),
        (_http(h.ForbiddenError, code=50013), ErrorClass.PERMANENT),
        # 404 Unknown Channel / Unknown Message -> permanent.
        (_http(h.NotFoundError, code=10003), ErrorClass.PERMANENT),
        (_http(h.NotFoundError, code=10008), ErrorClass.PERMANENT),
        # 400 malformed request -> permanent.
        (_http(h.BadRequestError, code=50035), ErrorClass.PERMANENT),
        (_http(h.BadRequestError, code=50006), ErrorClass.PERMANENT),
        # 401 unauthorized -> permanent.
        (
            h.UnauthorizedError(url="https://x", headers={}, raw_body="", message="m"),
            ErrorClass.PERMANENT,
        ),
        # 5xx -> transient.
        (
            h.InternalServerError(
                url="https://x", status=500, headers={}, raw_body="", message="m"
            ),
            ErrorClass.TRANSIENT,
        ),
        (
            h.HTTPResponseError(
                url="https://x", status=503, headers={}, raw_body="", message="m"
            ),
            ErrorClass.TRANSIENT,
        ),
        # 429 rate limited -> transient.
        (_rate_limit_too_long(), ErrorClass.TRANSIENT),
        # timeouts / connection errors -> transient.
        (TimeoutError(), ErrorClass.TRANSIENT),
        (ConnectionResetError(), ErrorClass.TRANSIENT),
        # unknown exception -> transient (and logged once).
        (ValueError("boom"), ErrorClass.TRANSIENT),
    ],
)
def test_classify_error(exc: BaseException, expected: ErrorClass) -> None:
    assert classify_error(exc) is expected


def test_reference_code_is_deterministic_per_identity() -> None:
    """Two exceptions with the same normalized identity share a reference code."""
    # The digit-run normalization collapses the differing snowflakes so both share
    # one identity -> one code.
    a = identity_for_exc(ValueError("failed for channel 123"))
    b = identity_for_exc(ValueError("failed for channel 456"))
    assert a == b
    assert reference_code(a) == reference_code(b)


def test_reference_code_differs_for_different_identities() -> None:
    a = reference_code(identity_for_exc(ValueError("nope")))
    b = reference_code(identity_for_exc(KeyError("nope")))
    assert a != b


def test_reference_code_shape() -> None:
    code = reference_code(identity_for_exc(ValueError("x")))
    assert len(code) == 6
    assert code == code.upper()
    assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in code)
