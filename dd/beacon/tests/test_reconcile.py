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

"""Unit tests for the reconcile target-map + newly_sent semantics (no DB)."""

import pytest

from dd.beacon.mirror_core import (
    KernelOutcome,
    KernelSuccess,
    KernelWorkControl,
    MirrorOperationType,
    build_reconcile_targets,
    kernel_work_control_registry,
)


@pytest.fixture(autouse=True)
def _clear_registry():
    yield
    kernel_work_control_registry._registry.clear()  # noqa: SLF001
    kernel_work_control_registry._locks.clear()  # noqa: SLF001


def test_reconcile_targets_edits_existing_sends_missing() -> None:
    # Desired dests: 10 (has a mirror), 11 (missing), 12 (missing). Source channel 1
    # is excluded. 13 has a stale recorded mirror but is no longer desired.
    desired = [10, 11, 12, 1]
    existing = {10: 100, 13: 130}
    targets = build_reconcile_targets(desired, existing, source_channel_id=1)

    # Existing dest -> its message id (edit); missing dests -> None (fresh send).
    assert targets[10] == 100
    assert targets[11] is None
    assert targets[12] is None
    # Stale recorded dest is still tracked (edited), not dropped.
    assert targets[13] == 130
    # Source channel excluded.
    assert 1 not in targets


def test_reconcile_targets_excludes_source_channel() -> None:
    targets = build_reconcile_targets([5, 6], {}, source_channel_id=5)
    assert set(targets) == {6}
    assert targets[6] is None


@pytest.mark.asyncio
async def test_newly_sent_after_reconcile_run() -> None:
    """newly_sent contains exactly the missing dests that succeeded."""
    desired = [10, 11, 12]
    existing = {10: 100}  # only 10 already has a mirror
    targets = build_reconcile_targets(desired, existing)

    async def kernel(ch_id: int, msg_id: int | None) -> KernelOutcome:
        # Edit branch keeps the existing id; send branch mints a new one.
        new_id = msg_id if msg_id is not None else ch_id * 1000
        return KernelSuccess(channel_id=ch_id, message_id=new_id)

    control = KernelWorkControl(
        source_channel_id=1,
        source_message_id=99,
        targets=targets,
        role_ping_per_ch_id={},
        mirror_operation_type=MirrorOperationType.UPDATE,
        kernel=kernel,
        retry_threshold=2,
    )
    await control.run_till_completion()

    # 10 was edited (kept id 100); 11 and 12 were freshly sent.
    assert control.successful_targets == {10: 100, 11: 11000, 12: 12000}
    assert control.newly_sent == {11: 11000, 12: 12000}
