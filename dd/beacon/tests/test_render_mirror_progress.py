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

"""Unit tests for the Components V2 mirror progress renderer (no Discord I/O)."""

from time import perf_counter

import hikari as h

from dd.beacon.extensions.mirror import (
    _CANCEL_CUSTOM_ID_PREFIX,
    render_mirror_progress,
)
from dd.beacon.mirror_core import (
    KernelFailure,
    KernelOutcome,
    KernelSuccess,
    KernelWorkControl,
    MirrorOperationType,
)
from dd.common import cfg
from dd.common.utils import ErrorClass


async def _noop_kernel(ch_id: int, msg_id: int | None) -> KernelOutcome:
    raise AssertionError("kernel should not be invoked in render tests")


def _control(op=MirrorOperationType.SEND, source_message_id=832):
    return KernelWorkControl(
        source_channel_id=1,
        source_message_id=source_message_id,
        targets={10: None, 11: None, 12: None},
        role_ping_per_ch_id={},
        mirror_operation_type=op,
        kernel=_noop_kernel,
        retry_threshold=3,
    )


def _render(control, *, enable_cancellation, final=False):
    return render_mirror_progress(
        control,
        title="Mirror send progress",
        source_message_link="https://discord.com/x",
        source_message_summary="Announcement",
        source_channel_link="https://discord.com/y",
        source_channel_name="news",
        start_time=perf_counter(),
        final=final,
        enable_cancellation=enable_cancellation,
    )


def _container(components) -> h.impl.ContainerComponentBuilder:
    assert len(components) == 1
    container = components[0]
    assert isinstance(container, h.impl.ContainerComponentBuilder)
    return container


def _cancel_custom_ids(container: h.impl.ContainerComponentBuilder) -> list[str]:
    ids: list[str] = []
    for comp in container.components:
        if isinstance(comp, h.impl.MessageActionRowBuilder):
            ids.extend(getattr(b, "custom_id", "") or "" for b in comp.components)
    return [cid for cid in ids if cid.startswith(_CANCEL_CUSTOM_ID_PREFIX)]


def test_default_accent_when_no_failures() -> None:
    control = _control()
    control._apply_outcome(KernelSuccess(channel_id=10, message_id=100))  # noqa: SLF001
    container = _container(_render(control, enable_cancellation=False))
    assert container.accent_color == cfg.embed_default_color


def test_error_accent_when_failures() -> None:
    control = _control()
    control.report_scheduled(10)
    control._apply_outcome(  # noqa: SLF001
        KernelFailure(
            channel_id=10,
            exc=ValueError("boom"),
            error_class=ErrorClass.PERMANENT,
            reference_code="PERM01",
        )
    )
    container = _container(_render(control, enable_cancellation=False))
    assert container.accent_color == cfg.embed_error_color
    # The failure breakdown surfaces the reference code.
    text = " ".join(
        c.content
        for c in container.components
        if isinstance(c, h.impl.TextDisplayComponentBuilder)
    )
    assert "PERM01" in text


def test_cancel_row_present_for_send_in_progress() -> None:
    control = _control(op=MirrorOperationType.SEND, source_message_id=832)
    container = _container(_render(control, enable_cancellation=True, final=False))
    ids = _cancel_custom_ids(container)
    assert ids == [f"{_CANCEL_CUSTOM_ID_PREFIX}:832"]


def test_cancel_row_namespaced_by_source_message_id() -> None:
    a = _container(_render(_control(source_message_id=111), enable_cancellation=True))
    b = _container(_render(_control(source_message_id=222), enable_cancellation=True))
    assert _cancel_custom_ids(a) != _cancel_custom_ids(b)


def test_no_cancel_row_when_final() -> None:
    control = _control()
    container = _container(_render(control, enable_cancellation=True, final=True))
    assert _cancel_custom_ids(container) == []


def test_no_cancel_row_when_disabled() -> None:
    # DELETE never enables cancellation.
    control = _control(op=MirrorOperationType.DELETE)
    container = _container(_render(control, enable_cancellation=False))
    assert _cancel_custom_ids(container) == []


def test_cancel_row_dropped_once_cancellation_requested() -> None:
    # Once cancel() fires (control.cancelled populated) the button is removed even
    # though work is still draining, so it can't be pressed twice.
    control = _control()
    control.cancel()
    container = _container(_render(control, enable_cancellation=True, final=False))
    assert _cancel_custom_ids(container) == []
    # Footer reflects the draining state.
    text = " ".join(
        c.content
        for c in container.components
        if isinstance(c, h.impl.TextDisplayComponentBuilder)
    )
    assert "Cancelling" in text
