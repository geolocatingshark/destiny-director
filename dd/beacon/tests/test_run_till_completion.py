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

"""Unit tests for :meth:`KernelWorkControl.run_till_completion` (worker pool).

Uses fake kernels so no Discord I/O / DB is required. Retry sleeps are zeroed via
monkeypatch so transient-retry paths run instantly.
"""

import asyncio as aio

import pytest

from dd.beacon.mirror_core import (
    KernelFailure,
    KernelSuccess,
    KernelWorkControl,
    MirrorOperationType,
    kernel_work_control_registry,
)
from dd.common import cfg
from dd.common.utils import ErrorClass


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cfg, "mirror_retry_min", 0, raising=False)
    monkeypatch.setattr(cfg, "mirror_retry_max", 0, raising=False)
    yield
    # Defensive: ensure the global registry is empty between tests.
    kernel_work_control_registry._registry.clear()  # noqa: SLF001
    kernel_work_control_registry._locks.clear()  # noqa: SLF001


def _control(targets, kernel, *, op=MirrorOperationType.SEND, retry_threshold=3):
    return KernelWorkControl(
        source_channel_id=1,
        source_message_id=99,
        targets=targets,
        role_ping_per_ch_id={},
        mirror_operation_type=op,
        kernel=kernel,
        retry_threshold=retry_threshold,
    )


@pytest.mark.asyncio
async def test_all_targets_succeed() -> None:
    async def kernel(ch_id, msg_id):
        return KernelSuccess(channel_id=ch_id, message_id=ch_id * 10)

    control = _control({1: None, 2: None, 3: None}, kernel)
    await control.run_till_completion()
    assert control.successful_targets == {1: 10, 2: 20, 3: 30}
    assert control.failed_targets == {}


@pytest.mark.asyncio
async def test_concurrency_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "mirror_max_concurrency", 3, raising=False)
    live = 0
    peak = 0

    async def kernel(ch_id, msg_id):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await aio.sleep(0.01)
        live -= 1
        return KernelSuccess(channel_id=ch_id, message_id=ch_id)

    control = _control({i: None for i in range(20)}, kernel)
    await control.run_till_completion()
    assert peak <= 3
    assert len(control.successful_targets) == 20


@pytest.mark.asyncio
async def test_tasks_set_does_not_grow_across_retries() -> None:
    # All transient failures so multiple retry rounds run; assert _tasks is released.
    async def kernel(ch_id, msg_id):
        return KernelFailure(
            channel_id=ch_id,
            exc=ValueError("x"),
            error_class=ErrorClass.TRANSIENT,
            reference_code="TRAN01",
        )

    control = _control({1: None, 2: None}, kernel, retry_threshold=3)
    await control.run_till_completion()
    # Each batch replaces _tasks then clears it; it must be empty at the end.
    assert control._tasks == set()  # noqa: SLF001
    assert set(control.failed_targets) == {1, 2}


@pytest.mark.asyncio
async def test_permanent_failure_not_retried() -> None:
    calls: dict[int, int] = {}

    async def kernel(ch_id, msg_id):
        calls[ch_id] = calls.get(ch_id, 0) + 1
        return KernelFailure(
            channel_id=ch_id,
            exc=ValueError("nope"),
            error_class=ErrorClass.PERMANENT,
            reference_code="PERM01",
        )

    control = _control({1: None}, kernel, retry_threshold=3)
    await control.run_till_completion()
    # Permanent -> tried exactly once, never retried.
    assert calls == {1: 1}
    assert set(control.failed_targets) == {1}


@pytest.mark.asyncio
async def test_transient_failure_retried_until_threshold() -> None:
    calls: dict[int, int] = {}

    async def kernel(ch_id, msg_id):
        calls[ch_id] = calls.get(ch_id, 0) + 1
        return KernelFailure(
            channel_id=ch_id,
            exc=ValueError("again"),
            error_class=ErrorClass.TRANSIENT,
            reference_code="TRAN01",
        )

    control = _control({1: None}, kernel, retry_threshold=2)
    await control.run_till_completion()
    assert calls == {1: 2}  # tried up to the threshold
    assert set(control.failed_targets) == {1}


@pytest.mark.asyncio
async def test_cancel_gracefully_drains(monkeypatch: pytest.MonkeyPatch) -> None:
    # One concurrency slot: only target 1 enters the kernel; 2 and 3 block on the
    # semaphore, so cancelling mid-flight must let 1 finish & record while 2/3 never
    # send (they hit the post-slot cancellation check).
    monkeypatch.setattr(cfg, "mirror_max_concurrency", 1, raising=False)
    started = aio.Event()
    release = aio.Event()
    sent: list[int] = []

    async def kernel(ch_id, msg_id):
        started.set()
        await release.wait()
        sent.append(ch_id)  # only reached by kernels actually allowed to run
        return KernelSuccess(channel_id=ch_id, message_id=ch_id)

    control = _control({1: None, 2: None, 3: None}, kernel)
    control_task = aio.create_task(control.run_till_completion())

    await started.wait()  # target 1's worker is inside the kernel, holding the slot
    control.cancel()
    # The in-flight worker is allowed to finish & record; the rest drain unsent.
    release.set()
    await control_task

    # Returned normally (no exception). Only the in-flight dest was sent + recorded.
    assert sent == [1]
    assert 1 in control.successful_targets
    assert 2 not in control.successful_targets
    assert 3 not in control.successful_targets
    assert {2, 3} <= set(control.cancelled)


@pytest.mark.asyncio
async def test_registry_and_lock_released_after_completion() -> None:
    """M1/M3: no per-source-message state pinned after a normal completion."""

    async def kernel(ch_id, msg_id):
        return KernelSuccess(channel_id=ch_id, message_id=ch_id)

    control = _control({1: None, 2: None}, kernel)
    await control.run_till_completion()

    assert kernel_work_control_registry._registry == {}  # noqa: SLF001
    assert kernel_work_control_registry._locks == {}  # noqa: SLF001
    assert control._tasks == set()  # noqa: SLF001
