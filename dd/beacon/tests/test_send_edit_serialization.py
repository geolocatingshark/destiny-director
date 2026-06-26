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

"""Unit tests for the per-source critical section + edit-supersedes-send takeover.

No Discord I/O / DB — fake kernels and the mirror_core registry primitives only. These
cover the machinery the repeater impls use to serialize a send's row-persist with an
edit's read/reconcile (closing the mid-fan-out double-send) and to let an edit take over
an in-flight send.
"""

import asyncio as aio

import pytest

from dd.beacon.mirror_core import (
    KernelSuccess,
    KernelWorkControl,
    MirrorOperationType,
    build_reconcile_targets,
    kernel_work_control_registry,
)
from dd.common import cfg


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cfg, "mirror_retry_min", 0, raising=False)
    monkeypatch.setattr(cfg, "mirror_retry_max", 0, raising=False)
    yield
    kernel_work_control_registry._registry.clear()  # noqa: SLF001
    kernel_work_control_registry._locks.clear()  # noqa: SLF001
    kernel_work_control_registry._lock_refcounts.clear()  # noqa: SLF001


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
async def test_lock_held_completes_and_cleans_up() -> None:
    """run_till_completion(lock_held=True) under a held critical section doesn't
    deadlock and leaves no per-source state pinned."""

    async def kernel(ch_id, msg_id):
        return KernelSuccess(channel_id=ch_id, message_id=ch_id)

    control = _control({1: None, 2: None}, kernel)
    async with kernel_work_control_registry.source_message_critical_section(1, 99):
        await control.run_till_completion(lock_held=True)

    assert control.successful_targets == {1: 1, 2: 2}
    assert kernel_work_control_registry._registry == {}  # noqa: SLF001
    assert kernel_work_control_registry._locks == {}  # noqa: SLF001
    assert kernel_work_control_registry._lock_refcounts == {}  # noqa: SLF001


@pytest.mark.asyncio
async def test_critical_section_serializes_same_source() -> None:
    order: list[str] = []

    async def worker(tag: str) -> None:
        async with kernel_work_control_registry.source_message_critical_section(1, 99):
            order.append(f"{tag}-start")
            await aio.sleep(0.01)
            order.append(f"{tag}-end")

    await aio.gather(worker("a"), worker("b"))

    # No interleaving: each entrant fully completes its inner region before the next.
    assert order in (
        ["a-start", "a-end", "b-start", "b-end"],
        ["b-start", "b-end", "a-start", "a-end"],
    )


@pytest.mark.asyncio
async def test_cancel_if_active_noop_when_absent() -> None:
    assert kernel_work_control_registry.cancel_if_active(1, 99) is False


@pytest.mark.asyncio
async def test_cancel_if_active_supersedes_inflight_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "mirror_max_concurrency", 1, raising=False)
    started = aio.Event()
    release = aio.Event()
    sent: list[int] = []

    async def kernel(ch_id, msg_id):
        if ch_id == 1:
            started.set()
            await release.wait()
        sent.append(ch_id)
        return KernelSuccess(channel_id=ch_id, message_id=ch_id * 100)

    control = _control({1: None, 2: None, 3: None}, kernel)

    async def run_send() -> None:
        async with kernel_work_control_registry.source_message_critical_section(1, 99):
            await control.run_till_completion(lock_held=True)

    task = aio.create_task(run_send())
    await started.wait()  # dest 1 is in-flight, holding the only slot

    superseded = kernel_work_control_registry.cancel_if_active(
        1, 99, superseded_by_edit=True
    )
    release.set()
    await task

    assert superseded is True
    assert control.superseded_by_edit is True
    # Only the in-flight dest sent + recorded; the rest drained unsent.
    assert sent == [1]
    assert control.successful_targets == {1: 100}
    assert {2, 3} <= set(control.cancelled)


@pytest.mark.asyncio
async def test_cancel_if_active_skips_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg, "mirror_max_concurrency", 1, raising=False)
    started = aio.Event()
    release = aio.Event()

    async def kernel(ch_id, msg_id):
        started.set()
        await release.wait()
        return KernelSuccess(channel_id=ch_id, message_id=ch_id)

    control = _control({1: 10}, kernel, op=MirrorOperationType.DELETE)
    task = aio.create_task(control.run_till_completion())
    await started.wait()

    # A delete in progress is never superseded by an edit.
    assert (
        kernel_work_control_registry.cancel_if_active(1, 99, superseded_by_edit=True)
        is False
    )
    release.set()
    await task
    assert control.superseded_by_edit is False


@pytest.mark.asyncio
async def test_supersede_then_reconcile_sends_each_dest_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The end-to-end invariant: an edit superseding a mid-fan-out send results in each
    dest handled exactly once — the in-flight dest is edited, the rest freshly sent."""
    monkeypatch.setattr(cfg, "mirror_max_concurrency", 1, raising=False)
    sends: list[int] = []
    edits: list[int] = []
    started = aio.Event()
    release = aio.Event()

    async def send_kernel(ch_id, msg_id):
        if ch_id == 1:
            started.set()
            await release.wait()
        sends.append(ch_id)
        return KernelSuccess(channel_id=ch_id, message_id=ch_id * 100)

    send_control = _control({1: None, 2: None, 3: None}, send_kernel)
    recorded: dict[int, int] = {}  # stands in for persisted MirroredMessage rows

    async def run_send() -> None:
        async with kernel_work_control_registry.source_message_critical_section(1, 99):
            await send_control.run_till_completion(lock_held=True)
            # Persist the freshly-sent pairs *inside* the lock, as the impl does.
            recorded.update(send_control.newly_sent)

    send_task = aio.create_task(run_send())
    await started.wait()
    assert kernel_work_control_registry.cancel_if_active(1, 99, superseded_by_edit=True)
    release.set()
    await send_task

    # Send drained: only dest 1 sent + recorded.
    assert sends == [1]
    assert recorded == {1: 100}

    # The edit's reconcile reads the persisted rows: dest 1 -> edit, 2 & 3 -> send.
    async def edit_kernel(ch_id, msg_id):
        (edits if msg_id is not None else sends).append(ch_id)
        return KernelSuccess(channel_id=ch_id, message_id=msg_id or ch_id * 100)

    targets = build_reconcile_targets([1, 2, 3], dict(recorded))
    update_control = KernelWorkControl(
        source_channel_id=1,
        source_message_id=99,
        targets=targets,
        role_ping_per_ch_id={},
        mirror_operation_type=MirrorOperationType.UPDATE,
        kernel=edit_kernel,
        retry_threshold=2,
    )
    async with kernel_work_control_registry.source_message_critical_section(1, 99):
        await update_control.run_till_completion(lock_held=True)

    # No double-send: dest 1 edited once; dests 2 & 3 each sent once.
    assert sorted(edits) == [1]
    assert sorted(sends) == [1, 2, 3]
