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

"""Unit tests for the token-bucket :class:`RateLimiter`."""

import asyncio as aio
import time

import pytest

from dd.beacon.mirror_core import RateLimiter


@pytest.mark.asyncio
async def test_n_acquisitions_take_at_least_expected_time() -> None:
    """N acquisitions at rate R take >= (N - 1) / R seconds.

    The bucket starts full (capacity == rate), so the first ``rate`` tokens are
    immediate; we pick N just above capacity so the limiter has to wait at least once.
    """
    rate = 20.0
    n = 25  # 5 past the initial full bucket of 20
    limiter = RateLimiter(rate)
    start = time.monotonic()
    for _ in range(n):
        await limiter.acquire()
    elapsed = time.monotonic() - start
    # The 5 over-capacity acquisitions each wait ~1/rate. Use a slightly relaxed
    # lower bound to absorb scheduler jitter.
    min_expected = (n - rate) / rate
    assert elapsed >= min_expected * 0.8


@pytest.mark.asyncio
async def test_does_not_bank_unbounded_burst() -> None:
    """Idle time cannot bank more than ``rate`` tokens (capacity cap)."""
    rate = 10.0
    limiter = RateLimiter(rate)
    # Simulate a long idle period.
    limiter._updated -= 100  # type: ignore[attr-defined]  # noqa: SLF001
    limiter._tokens = 0.0  # type: ignore[attr-defined]  # noqa: SLF001
    # After refill the bucket is capped at ``rate``; draining ``rate`` is immediate,
    # the next one must wait.
    start = time.monotonic()
    for _ in range(int(rate)):
        await limiter.acquire()
    immediate = time.monotonic() - start
    assert immediate < 0.05
    start = time.monotonic()
    await limiter.acquire()
    assert time.monotonic() - start >= (1 / rate) * 0.8


@pytest.mark.asyncio
async def test_context_manager_acquires() -> None:
    limiter = RateLimiter(100.0)
    async with limiter:
        pass  # no error, token consumed


def test_rate_must_be_positive() -> None:
    with pytest.raises(ValueError):
        RateLimiter(0)
    with pytest.raises(ValueError):
        RateLimiter(-1)


@pytest.mark.asyncio
async def test_concurrent_acquire_is_serialized() -> None:
    """Concurrent acquirers do not over-draw: total honoured the rate bound."""
    rate = 50.0
    limiter = RateLimiter(rate)
    n = 70
    start = time.monotonic()
    await aio.gather(*(limiter.acquire() for _ in range(n)))
    elapsed = time.monotonic() - start
    assert elapsed >= ((n - rate) / rate) * 0.8
