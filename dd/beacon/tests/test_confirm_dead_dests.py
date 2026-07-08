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

"""Unit tests for ``mirror._confirm_dead_dests`` — the gate that turns this run's
PERMANENT failures into the set that counts toward auto-disable (no DB / network).

This pins the core false-positive prevention: a PERMANENT failure whose perm verdict is
SENDABLE/UNKNOWN (e.g. a malformed-payload 50035) must be EXCLUDED, and a probe that
itself errors must be treated as "not confirmed" and must never propagate (else it would
abort the caller's post-run MirroredMessage persistence)."""

import typing as t
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from dd.beacon.extensions import mirror
from dd.beacon.mirror_core import KernelWorkControl
from dd.beacon.utils import DestVerdict

pytestmark = pytest.mark.asyncio


def _control(permanent_ids: list[int]) -> KernelWorkControl:
    # _confirm_dead_dests only touches .permanent_failed_targets and .failures; a light
    # stub avoids standing up a full control + kernel.
    stub = SimpleNamespace(
        permanent_failed_targets={cid: None for cid in permanent_ids},
        failures={
            cid: SimpleNamespace(reference_code=f"REF{cid}") for cid in permanent_ids
        },
    )
    return t.cast(KernelWorkControl, stub)


async def test_only_confirmed_dead_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    verdicts = {
        10: DestVerdict.CONFIRMED_GONE,
        11: DestVerdict.CONFIRMED_UNSENDABLE,
        12: DestVerdict.SENDABLE,  # perms fine but PERMANENT (e.g. 50035) — excluded
        13: DestVerdict.UNKNOWN,  # undeterminable — excluded
    }
    monkeypatch.setattr(
        mirror.utils,
        "confirm_dest_unsendable",
        AsyncMock(side_effect=lambda _bot, cid: verdicts[cid]),
    )
    dead = await mirror._confirm_dead_dests(MagicMock(), _control([10, 11, 12, 13]), 1)
    assert sorted(dead) == [10, 11]


async def test_probe_error_is_not_confirmed_and_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A flaky fetch (5xx / rate-limit) while probing one dest must not abort the whole
    # post-run persistence — it degrades to "not confirmed" (bias: don't disable).
    async def flaky(_bot: object, cid: int) -> DestVerdict:
        if cid == 11:
            raise RuntimeError("simulated REST 5xx")
        return DestVerdict.CONFIRMED_GONE

    monkeypatch.setattr(mirror.utils, "confirm_dest_unsendable", flaky)
    dead = await mirror._confirm_dead_dests(MagicMock(), _control([10, 11]), 1)
    assert dead == [10]  # 11 errored → excluded, no exception propagated


async def test_no_permanent_failures_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe = AsyncMock()
    monkeypatch.setattr(mirror.utils, "confirm_dest_unsendable", probe)
    assert await mirror._confirm_dead_dests(MagicMock(), _control([]), 1) == []
    probe.assert_not_awaited()
