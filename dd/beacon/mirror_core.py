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

"""Pure mirror scheduling helpers, free of Discord I/O so they are directly testable.

- :class:`MirrorOperationType` — the send / update / delete tag carried on a run.
- the global token-bucket :class:`RateLimiter` (and shared :data:`rate_limiter`),
  bounding the Discord request rate across every mirror run.
- :class:`RunCounts` — a run's per-state progress, derived from a cheap ledger
  ``GROUP BY state`` count (the single source of truth for progress), plus
  :class:`RunView` (its display identity) and :class:`FailureGroup` (the grouped
  failure summary for the progress card).
"""

import asyncio as aio
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from ..common import cfg
from ..common.schemas import DeliveryState
from ..common.utils import ErrorClass


class MirrorOperationType(Enum):
    """Enum to represent the type of mirror operation"""

    SEND = 1
    UPDATE = 2
    DELETE = 3


class RateLimiter:
    """A token-bucket rate limiter: refills ``rate`` tokens/second, no sleep on release.

    Unlike a ``TimedSemaphore`` (which conflates concurrency with rate by sleeping a
    full period on every ``__aexit__``), this bounds *only* the global request rate.
    Concurrency is bounded separately by the worker pool's semaphore. Capacity is
    capped at ``rate`` so a long idle period cannot bank a burst far above the steady
    rate.
    """

    def __init__(self, rate: float):
        if rate <= 0:
            raise ValueError("rate must be positive")
        self._rate = float(rate)
        self._capacity = float(rate)
        self._tokens = float(rate)
        self._updated = time.monotonic()
        self._lock = aio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._updated = now

    async def acquire(self) -> None:
        """Block until a token is available, then consume it."""
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                # Sleep just long enough for the next token to accrue.
                deficit = 1 - self._tokens
                await aio.sleep(deficit / self._rate)

    async def __aenter__(self) -> "RateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, *exc: object) -> None:
        # No release: a token-bucket does not return tokens, it refills over time.
        return None


# One global token-bucket shared across every mirror run / operation type so a large
# fan-out can never consume more than ``cfg.mirror_rate_per_sec`` of Discord's global
# REST budget, leaving headroom for interactive commands and other bot functions.
rate_limiter = RateLimiter(cfg.mirror_rate_per_sec)


@dataclass(frozen=True, slots=True)
class FailureGroup:
    """One reference-code's worth of failures, for the progress UI / alert summary."""

    reference_code: str
    count: int
    error_class: ErrorClass
    sample_message: str


@dataclass(frozen=True, slots=True)
class RunCounts:
    """A run's progress, derived from a ledger ``GROUP BY state`` count.

    The single source of truth for run progress — the progress card renders straight off
    this, so there is no accounting to drift from the ledger. A run is complete
    once no rows are still ``PENDING`` (crosspost is background work and does not
    hold a run open).
    """

    delivered: int = 0
    failed: int = 0
    cancelled: int = 0
    pending: int = 0

    @classmethod
    def from_state_counts(cls, counts: Mapping[str, int]) -> "RunCounts":
        return cls(
            delivered=counts.get(DeliveryState.DELIVERED.value, 0),
            failed=counts.get(DeliveryState.FAILED.value, 0),
            cancelled=counts.get(DeliveryState.CANCELLED.value, 0),
            pending=counts.get(DeliveryState.PENDING.value, 0),
        )

    @property
    def total(self) -> int:
        return self.delivered + self.failed + self.cancelled + self.pending

    @property
    def resolved(self) -> int:
        "Rows in a terminal state (drives completion)."
        return self.delivered + self.failed + self.cancelled

    @property
    def throughput_resolved(self) -> int:
        "Resolved excluding cancels — the rate/ETA denominator."
        return self.delivered + self.failed

    @property
    def is_complete(self) -> bool:
        "A run is done once nothing is left to deliver."
        return self.pending == 0


@dataclass
class RunView:
    """Display identity for one ledger-backed mirror run (per source message).

    Holds only what the progress card needs to label itself; the live progress numbers
    come from :class:`RunCounts` (a ledger count), refreshed on each render.
    """

    op: MirrorOperationType
    src_ch_id: int | None
    src_msg_id: int
    start_time: float  # perf_counter()
    counts: RunCounts = field(default_factory=RunCounts)
