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

The mirror subsystem broadcasts one source-channel message to many destination
channels. Since the ``mirror-v2`` ledger rewrite, the fan-out itself lives in
:mod:`dd.beacon.mirror_worker`; this module holds only the pure survivors:

    - :class:`MirrorOperationType` — the send / update / delete tag carried on a run.
    - the global token-bucket :class:`RateLimiter` (and shared :data:`rate_limiter`),
      bounding the Discord request rate across every mirror run.
    - :class:`RunView` — the in-memory progress accounting for one ledger-backed run,
      plus :class:`RunFailure` / :class:`FailureGroup` for its grouped failure summary.
"""

import asyncio as aio
import time
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

from ..common import cfg
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
class RunFailure:
    """One destination's failure as recorded in a :class:`RunView`.

    Carries the classified reference code + a representative message (for the grouped
    breakdown) and whether the perm probe *confirmed* the dest dead (only confirmed-dead
    permanent failures feed the cross-run auto-disable).
    """

    reference_code: str
    error_class: ErrorClass
    sample_message: str
    confirmed_dead: bool = False


@dataclass
class RunView:
    """In-memory progress view for one ledger-backed mirror run (per source message).

    The minimal in-memory tracker for a run: it holds *only* what the progress UI
    and the run-end hook need, fed by outcome-recording calls from the
    convergence worker (and the cancel command). Terminal-state accounting is by
    destination-channel id (sets), so retries and races stay idempotent; the progress
    counts are the set sizes.

    ``total`` is the count of non-terminal rows at registration. A run is *complete*
    once every dest reaches a terminal state (delivered / failed / cancelled) or the run
    was superseded by an edit; ``finalized`` is set by the run-end hook once the disable
    sweep + alerts have run, gating the progress card's final render.
    """

    op: MirrorOperationType
    src_ch_id: int | None
    src_msg_id: int
    total: int
    start_time: float  # perf_counter()
    _delivered: set[int] = field(default_factory=set)
    _cancelled: set[int] = field(default_factory=set)
    attempted_once: set[int] = field(default_factory=set)
    # The failed set is derived from ``failures.keys()`` — every recorder keeps the two
    # in lock-step, so a separate set would just be redundant state to keep in sync.
    failures: dict[int, RunFailure] = field(default_factory=dict)
    cancel_requested: bool = False
    # ``superseded`` — an edit or delete handed this run over to a successor, so its
    # run-end reporting (alerts, disable sweep) is skipped and it finalizes immediately.
    # ``superseded_by_edit`` is the display flavour (recycle vs. plain "superseded").
    superseded: bool = False
    superseded_by_edit: bool = False
    disabled_count: int = 0
    finalized: bool = False
    _finalizing: bool = False

    # -- recording ---------------------------------------------------------

    def on_delivered(self, dest_ch_id: int) -> None:
        """Record a converged (send/edit/delete) destination.

        Idempotent under retries and clears any prior failure/cancel for the dest, so a
        transient-then-success dest ends up counted only as delivered.
        """
        self.attempted_once.add(dest_ch_id)
        self._cancelled.discard(dest_ch_id)
        self.failures.pop(dest_ch_id, None)
        self._delivered.add(dest_ch_id)

    def on_transient(self, dest_ch_id: int) -> None:
        """Record a retryable failure (attempted, not yet resolved → 'retrying')."""
        self.attempted_once.add(dest_ch_id)

    def on_failed(self, dest_ch_id: int, failure: RunFailure) -> None:
        """Record a terminal (permanent / attempt-exhausted) failure."""
        self.attempted_once.add(dest_ch_id)
        self._cancelled.discard(dest_ch_id)
        self.failures[dest_ch_id] = failure

    def on_cancelled(self, dest_ch_id: int) -> None:
        """Record a cancelled destination (short-circuited before any Discord call)."""
        if dest_ch_id in self._delivered or dest_ch_id in self.failures:
            return
        self._cancelled.add(dest_ch_id)

    # -- counts (progress UI) ---------------------------------------------

    @property
    def delivered(self) -> int:
        return len(self._delivered)

    @property
    def failed(self) -> int:
        return len(self.failures)

    @property
    def cancelled_count(self) -> int:
        return len(self._cancelled)

    @property
    def retrying(self) -> int:
        "Attempted at least once but not yet in a terminal state."
        return len(
            self.attempted_once
            - self._delivered
            - self.failures.keys()
            - self._cancelled
        )

    @property
    def not_yet_tried(self) -> int:
        "Destinations with no attempt yet (and not cancelled outright)."
        return max(0, self.total - len(self.attempted_once | self._cancelled))

    @property
    def resolved(self) -> int:
        "Destinations in a terminal state (drives completion)."
        return self.delivered + self.failed + self.cancelled_count

    @property
    def throughput_resolved(self) -> int:
        "Resolved excluding cancels — the rate/ETA denominator (matches the old UI)."
        return self.delivered + self.failed

    @property
    def is_complete(self) -> bool:
        """Best-effort in-memory completion, for the progress *display* only.

        Authoritative finalization is ledger-driven (the worker reconciles a run against
        its ``mirror_delivery`` rows): in-memory set accounting can momentarily read
        complete while the ledger still has PENDING rows (e.g. an outcome the version
        guard bounced back), so the worker must not finalize on this alone.
        """
        return self.superseded or self.superseded_by_edit or self.resolved >= self.total

    @property
    def has_permanent(self) -> bool:
        return any(
            f.error_class is ErrorClass.PERMANENT for f in self.failures.values()
        )

    @property
    def not_confirmed_dead(self) -> dict[int, RunFailure]:
        """Permanent failures the perm probe did NOT confirm dead — surfaced in one
        aggregated warning and left for a human (never auto-disabled)."""
        return {
            dest: f
            for dest, f in self.failures.items()
            if f.error_class is ErrorClass.PERMANENT and not f.confirmed_dead
        }

    @property
    def failure_breakdown(self) -> list[FailureGroup]:
        """Group failures by reference code, most-common first."""
        counts: Counter[str] = Counter(
            failure.reference_code for failure in self.failures.values()
        )
        representative: dict[str, RunFailure] = {}
        for failure in self.failures.values():
            representative.setdefault(failure.reference_code, failure)
        return [
            FailureGroup(
                reference_code=code,
                count=count,
                error_class=representative[code].error_class,
                sample_message=representative[code].sample_message,
            )
            for code, count in counts.most_common()
        ]
