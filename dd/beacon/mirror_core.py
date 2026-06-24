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

"""Pure mirror-fan-out logic, free of Discord I/O so it is directly unit-testable.

The mirror subsystem broadcasts one source-channel message to many destination
channels. This module holds the *accounting and scheduling* core — kernel outcome
types, the work tracker/controller, the per-source-message registry, and a global
token-bucket :class:`RateLimiter` — while ``dd.beacon.extensions.mirror`` supplies
the Discord-touching kernels and progress UI.

Design:
    - A **kernel** is an ``async (ch_id, msg_id) -> KernelOutcome`` callable that does
      the actual API work for one destination and *returns* a ``KernelSuccess`` /
      ``KernelFailure`` rather than mutating shared state or raising.
    - :class:`KernelWorkTracker` is the single owner of per-run state; all mutation
      flows through :meth:`KernelWorkTracker._apply_outcome`.
    - :class:`KernelWorkControl` schedules kernels through a bounded worker pool
      (concurrency capped by ``cfg.mirror_max_concurrency``), with the global rate
      bound applied *inside* each kernel via the shared :data:`rate_limiter`.
    - :meth:`KernelWorkControl.cancel` **gracefully drains**: it stops scheduling new
      targets but lets in-flight API calls finish and record, so a destination can
      never be sent Discord-side yet left unrecorded (which would double-send on a
      later reconcile).
"""

import asyncio as aio
import collections.abc
import time
import typing as t
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import Enum
from random import randint

from ..common import cfg
from ..common.utils import ErrorClass


class MirrorOperationType(Enum):
    """Enum to represent the type of mirror operation"""

    SEND = 1
    UPDATE = 2
    DELETE = 3


@dataclass(frozen=True, slots=True)
class KernelSuccess:
    """A destination handled successfully.

    ``message_id`` is the destination message id — the existing id for an edit/delete
    and the freshly-created id for a send.
    """

    channel_id: int
    message_id: int


@dataclass(frozen=True, slots=True)
class KernelFailure:
    """A destination that failed, with its classification and reference code."""

    channel_id: int
    exc: BaseException
    error_class: ErrorClass
    reference_code: str


KernelOutcome = KernelSuccess | KernelFailure


class MirrorKernel(t.Protocol):
    """The shape of a kernel: do one destination's API work, return its outcome."""

    async def __call__(self, ch_id: int, msg_id: int | None) -> KernelOutcome: ...


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


class KernelWorkControlRegistry:
    """Registry to keep track of current KernelWorkControl instances"""

    def __init__(self):
        # Note: the dict is structured as follows:
        # {
        #    (
        #       source_channel_id,
        #       source_message_id,
        #   ): KernelWorkControl
        # }
        self._registry: dict[tuple[int | None, int], KernelWorkControl] = {}
        self._locks: dict[tuple[int | None, int], aio.Lock] = defaultdict(aio.Lock)

    def register(self, control: "KernelWorkControl"):
        """Register a KernelWorkControl instance"""
        key = (
            control.source_channel_id,
            control.source_message_id,
        )
        if key in self._registry:
            existing_control = self._registry[key]
            if existing_control.is_work_left_to_do:
                # Consider making this an async wait for the other task to finish
                # Remember to alter the code for whether we will block for all
                # operation types individually or cumulatively if you do make this
                # change
                raise ValueError(f"KernelWorkControl already registered for {key}")
        self._registry[key] = control

    def lock_source_message(self, control: "KernelWorkControl") -> aio.Lock:
        """Wait for the lock for a KernelWorkControl instance"""
        key = (
            control.source_channel_id,
            control.source_message_id,
        )
        return self._locks[key]

    def release(self, control: "KernelWorkControl") -> None:
        """Drop a finished control's registry entry (and its lock when free).

        Called from :meth:`KernelWorkControl.run_till_completion`'s ``finally`` so a
        completed operation leaves nothing pinned for the process lifetime (the M1/M3
        leaks). ``register`` already rejects a second op for the same key while work
        is in progress, so by the time we get here there is no competing in-flight op
        for this key and popping is safe. The lock is only evicted when not held/
        awaited, to avoid deleting one another coroutine is waiting on.
        """
        key = (control.source_channel_id, control.source_message_id)
        # Only drop the registry entry if it still points at *this* control; a later
        # op for the same key may have replaced it.
        if self._registry.get(key) is control:
            del self._registry[key]
        lock = self._locks.get(key)
        if lock is not None and not lock.locked():
            del self._locks[key]

    def cancel(
        self,
        source_channel_id: int | None,
        source_message_id: int,
    ):
        """Cancel a KernelWorkControl instance.

        Permitted for SEND and UPDATE (DELETE runs to completion). The cancel
        gracefully drains in-flight work; the control's own ``run_till_completion``
        ``finally`` removes the registry entry, so we do **not** pop it here.
        """
        key = (source_channel_id, source_message_id)
        if key in self._registry:
            control = self._registry[key]
            if control.mirror_operation_type == MirrorOperationType.DELETE:
                raise ValueError(
                    "Can only cancel mirror sends and updates. This message has an "
                    f"operation of type '{control.mirror_operation_type}' running"
                )
            control.cancel()
        else:
            raise ValueError("This message does not have any operations in progress")


kernel_work_control_registry = KernelWorkControlRegistry()


class KernelWorkTracker:
    """Tracks the progress of all kernels for a particular mirror event.

    ``targets`` maps ``{target_channel_id: target_message_id | None}`` — the message
    id is ``None`` for a fresh send and the existing dest message id for an edit /
    delete (or for a reconcile dest that already has a mirrored message).
    """

    def __init__(
        self,
        source_channel_id: int | None,
        source_message_id: int,
        targets: collections.abc.Mapping[int, int | None],
        mirror_operation_type: MirrorOperationType,
        retry_threshold: int = 3,
    ):
        self.retry_threshold = retry_threshold
        self.source_channel_id = source_channel_id
        self.source_message_id = source_message_id
        self.mirror_operation_type = mirror_operation_type
        self._targets = dict(targets)
        # The targets' *initial* message ids, frozen so ``newly_sent`` can tell which
        # successes started life as a fresh send (initial id None) vs an edit.
        self._initial_targets: dict[int, int | None] = dict(targets)
        self._tries: dict[int, int] = {target_id: 0 for target_id in self._targets}
        self._completed_successfully: dict[int, int] = {}
        self._scheduled: dict[int, int | None] = {}
        self._failures: dict[int, KernelFailure] = {}
        self._permanently_failed: set[int] = set()
        self.cancelled: dict[int, int | None] = {}
        self._cancelled: bool = False

    # -- mutation ----------------------------------------------------------

    def _report_try(self, channel_id: int):
        self._tries[channel_id] += 1
        self._scheduled.pop(channel_id, None)

    def report_scheduled(self, channel_id: int, message_id: int | None = None):
        if channel_id in self._scheduled:
            raise ValueError(
                f"Target already scheduled. "
                f"source_message_id: {self.source_message_id} id:"
                f"{channel_id}.{self._scheduled[channel_id]} vs incoming: "
                f"{channel_id}.{message_id}"
            )
        self._scheduled[channel_id] = message_id

    def report_completed(self, channel_id: int, message_id: int):
        self._report_try(channel_id)
        self._completed_successfully[channel_id] = message_id

    def report_failure(self, channel_id: int):
        self._report_try(channel_id)

    def _apply_outcome(self, outcome: KernelOutcome) -> None:
        """The single place run state mutates in response to a kernel result."""
        if isinstance(outcome, KernelSuccess):
            self.report_completed(outcome.channel_id, outcome.message_id)
            return
        # KernelFailure
        self._failures[outcome.channel_id] = outcome
        self.report_failure(outcome.channel_id)
        if outcome.error_class is ErrorClass.PERMANENT:
            self._permanently_failed.add(outcome.channel_id)

    # -- views -------------------------------------------------------------

    @property
    def failed_targets(self) -> dict[int, int | None]:
        "IDs that will not be retried: hit the retry threshold or permanently failed."
        return {
            channel_id: message_id
            for channel_id, message_id in self._targets.items()
            if (
                self._tries[channel_id] >= self.retry_threshold
                or channel_id in self._permanently_failed
            )
            and channel_id not in self._completed_successfully
        }

    @property
    def successful_targets(self) -> dict[int, int]:
        return self._completed_successfully

    @property
    def newly_sent(self) -> dict[int, int]:
        """Successes whose target started as a fresh send (initial msg id ``None``).

        Used by the SEND / reconcile post-run DB write to record only the *new*
        ``MirroredMessage`` pairs — edited destinations already have their pair.
        """
        return {
            channel_id: message_id
            for channel_id, message_id in self._completed_successfully.items()
            if self._initial_targets.get(channel_id) is None
        }

    @property
    def failures(self) -> dict[int, KernelFailure]:
        return self._failures

    @property
    def total_targets(self) -> int:
        return len(self._targets)

    @property
    def _targets_tried_at_least_once(self) -> dict[int, int | None]:
        return {
            target: self._targets[target]
            for target, tries_ in self._tries.items()
            if tries_ > 0
        }

    @property
    def targets_not_yet_tried(self) -> dict[int, int | None]:
        return {
            target: self._targets[target]
            for target, tries_ in self._tries.items()
            if tries_ == 0
        }

    @property
    def is_every_target_tried(self) -> bool:
        return len(self._targets_tried_at_least_once) == len(self._targets)

    @property
    def targets_to_schedule(self) -> dict[int, int | None]:
        if self._cancelled:
            # Graceful drain: stop scheduling anything new (incl. retries).
            return {}
        return {
            channel_id: message_id
            for channel_id, message_id in self._targets.items()
            if channel_id not in self._scheduled
            and channel_id not in self.failed_targets
            and channel_id not in self._permanently_failed
            and channel_id not in self.successful_targets
            and channel_id not in self.cancelled
        }

    @property
    def targets_being_retried(self) -> dict[int, int | None]:
        return {
            channel_id: message_id
            for channel_id, message_id in self._targets.items()
            if channel_id in self._targets_tried_at_least_once
            and channel_id not in self.failed_targets
            and channel_id not in self.successful_targets
        }

    @property
    def failure_breakdown(self) -> "list[FailureGroup]":
        """Group failures by reference code with a representative message + class.

        One entry per distinct reference code, ordered most-common first, so the
        progress UI and the aggregated alert can show ``ref_code ×count`` instead of
        N per-channel lines.
        """
        counts: Counter[str] = Counter(
            failure.reference_code for failure in self._failures.values()
        )
        representative: dict[str, KernelFailure] = {}
        for failure in self._failures.values():
            representative.setdefault(failure.reference_code, failure)
        return [
            FailureGroup(
                reference_code=code,
                count=count,
                error_class=representative[code].error_class,
                sample_message=str(representative[code].exc),
            )
            for code, count in counts.most_common()
        ]

    @property
    def is_work_left_to_do(self) -> bool:
        return bool(self.targets_to_schedule) or bool(self._scheduled)


@dataclass(frozen=True, slots=True)
class FailureGroup:
    """One reference-code's worth of failures, for the progress UI / alert summary."""

    reference_code: str
    count: int
    error_class: ErrorClass
    sample_message: str


class KernelWorkControl(KernelWorkTracker):
    def __init__(
        self,
        source_channel_id: int | None,
        source_message_id: int,
        targets: collections.abc.Mapping[int, int | None],
        role_ping_per_ch_id: collections.abc.Mapping[int, int | None],
        mirror_operation_type: MirrorOperationType,
        kernel: MirrorKernel,
        retry_threshold: int = 3,
    ):
        super().__init__(
            source_channel_id,
            source_message_id,
            targets,
            mirror_operation_type=mirror_operation_type,
            retry_threshold=retry_threshold,
        )
        self.role_ping_per_ch_id = role_ping_per_ch_id
        # The kernel is supplied at construction time; it does one destination's API
        # work and returns a KernelOutcome (it should not raise or mutate the tracker).
        self._kernel: MirrorKernel = kernel
        self._tasks: set[aio.Task[None]] = set()

    async def run_till_completion(self):
        try:
            async with kernel_work_control_registry.lock_source_message(self):
                kernel_work_control_registry.register(self)

                loop_number = 0
                while self.is_work_left_to_do and loop_number < self.retry_threshold:
                    await self._run_batch(
                        list(self.targets_to_schedule.items()),
                        # First pass runs immediately; retries wait a randomised
                        # few minutes (outside the concurrency slot) to spread load.
                        delay=loop_number != 0,
                    )
                    loop_number += 1
        finally:
            # Release per-source-message state so a completed (or cancelled) op leaves
            # nothing pinned for the process lifetime (M1/M3 leaks). Runs *after* the
            # ``async with`` has released the lock, so ``release`` can evict the now
            # unheld lock.
            kernel_work_control_registry.release(self)

    async def _run_batch(
        self, batch: list[tuple[int, int | None]], *, delay: bool
    ) -> None:
        sem = aio.Semaphore(cfg.mirror_max_concurrency)

        async def worker(ch_id: int, msg_id: int | None) -> None:
            self.report_scheduled(ch_id, msg_id)
            if delay:
                # Retry sleep happens OUTSIDE the concurrency slot so it neither
                # blocks the round nor holds a worker, and outside the rate limiter.
                await aio.sleep(randint(cfg.mirror_retry_min, cfg.mirror_retry_max))
            async with sem:
                # Re-check cancellation *after* acquiring the slot. A worker may have
                # been waiting on the semaphore (or retry sleep) when cancel() fired;
                # only kernels that actually start their API call are allowed to run,
                # so a not-yet-started dest is never sent Discord-side. There is no
                # ``await`` between this check and the kernel call, so it is atomic
                # with respect to cancel() under the single-threaded event loop.
                if self._cancelled:
                    self._mark_cancelled(ch_id, msg_id)
                    return
                outcome = await self._kernel(ch_id, msg_id)
            self._apply_outcome(outcome)

        # Replace the task set each batch (don't grow it) so done tasks are released.
        tasks = [aio.create_task(worker(c, m)) for c, m in batch]
        self._tasks = set(tasks)
        try:
            await aio.gather(*tasks, return_exceptions=True)
        finally:
            self._tasks.clear()

    def _mark_cancelled(self, ch_id: int, msg_id: int | None) -> None:
        self._scheduled.pop(ch_id, None)
        self.cancelled[ch_id] = msg_id

    def cancel(self):
        """Gracefully drain: stop scheduling new targets, let in-flight ones finish.

        We do **not** ``task.cancel()`` running workers — that could leave a
        destination sent Discord-side but unrecorded in the DB, double-sending on a
        later reconcile. Instead we set ``_cancelled`` (so ``targets_to_schedule``
        returns empty and the worker's post-slot check short-circuits), and move every
        target that has neither finished nor started its API call to ``cancelled``.
        A worker already inside the kernel's API call will still finish and record via
        ``_apply_outcome``; one merely waiting on the semaphore/retry sleep will hit
        the post-slot ``_cancelled`` check and be marked cancelled instead.
        """
        self._cancelled = True
        # Record targets that have neither finished nor been handed to a live worker
        # (e.g. ones that would only have run in a later, now-suppressed retry round).
        # Targets currently in ``_scheduled`` have a live worker that will resolve
        # itself — either it is already inside the kernel's API call (and will record
        # success/failure) or it is waiting on the slot and will hit the post-slot
        # ``_cancelled`` check and mark itself cancelled.
        not_handled = {
            ch_id: msg_id
            for ch_id, msg_id in self._targets.items()
            if ch_id not in self._completed_successfully
            and ch_id not in self.failed_targets
            and ch_id not in self._scheduled
        }
        self.cancelled.update(not_handled)
