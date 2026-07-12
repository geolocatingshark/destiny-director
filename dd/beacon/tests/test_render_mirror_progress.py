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
from dd.beacon.mirror_core import MirrorOperationType, RunCounts, RunView
from dd.common import cfg


def _view(op=MirrorOperationType.SEND, source_message_id=832, **counts):
    view = RunView(
        op=op,
        src_ch_id=1,
        src_msg_id=source_message_id,
        start_time=perf_counter(),
    )
    view.counts = RunCounts(**counts)
    return view


def _render(view, *, enable_cancellation, final=False, breakdown=None):
    return render_mirror_progress(
        view,
        title="Mirror send progress",
        source_message_link="https://discord.com/x",
        source_message_summary="Announcement",
        source_channel_link="https://discord.com/y",
        source_channel_name="news",
        final=final,
        enable_cancellation=enable_cancellation,
        breakdown=breakdown,
    )


def _container(components) -> h.impl.ContainerComponentBuilder:
    assert len(components) == 1
    container = components[0]
    assert isinstance(container, h.impl.ContainerComponentBuilder)
    return container


def _text(container: h.impl.ContainerComponentBuilder) -> str:
    return " ".join(
        c.content
        for c in container.components
        if isinstance(c, h.impl.TextDisplayComponentBuilder)
    )


def _cancel_custom_ids(container: h.impl.ContainerComponentBuilder) -> list[str]:
    ids: list[str] = []
    for comp in container.components:
        if isinstance(comp, h.impl.MessageActionRowBuilder):
            ids.extend(getattr(b, "custom_id", "") or "" for b in comp.components)
    return [cid for cid in ids if cid.startswith(_CANCEL_CUSTOM_ID_PREFIX)]


def test_default_accent_when_no_failures() -> None:
    container = _container(
        _render(_view(delivered=1, pending=2), enable_cancellation=False)
    )
    assert container.accent_color == cfg.embed_default_color


def test_error_accent_and_breakdown_when_failures() -> None:
    view = _view(failed=1, delivered=1)
    container = _container(
        _render(
            view,
            enable_cancellation=False,
            breakdown=[("PERM01", "PERMANENT", 1, "boom")],
        )
    )
    assert container.accent_color == cfg.embed_error_color
    assert "PERM01" in _text(container)  # the failure breakdown surfaces the ref code


def test_cancel_row_present_for_send_in_progress() -> None:
    view = _view(op=MirrorOperationType.SEND, source_message_id=832, pending=3)
    container = _container(_render(view, enable_cancellation=True, final=False))
    assert _cancel_custom_ids(container) == [f"{_CANCEL_CUSTOM_ID_PREFIX}:832"]


def test_cancel_row_namespaced_by_source_message_id() -> None:
    a = _container(
        _render(_view(source_message_id=111, pending=1), enable_cancellation=True)
    )
    b = _container(
        _render(_view(source_message_id=222, pending=1), enable_cancellation=True)
    )
    assert _cancel_custom_ids(a) != _cancel_custom_ids(b)


def test_no_cancel_row_when_final() -> None:
    container = _container(
        _render(_view(delivered=3), enable_cancellation=True, final=True)
    )
    assert _cancel_custom_ids(container) == []


def test_no_cancel_row_when_disabled() -> None:
    # DELETE never enables cancellation.
    view = _view(op=MirrorOperationType.DELETE, pending=3)
    container = _container(_render(view, enable_cancellation=False))
    assert _cancel_custom_ids(container) == []


def test_cancelled_footer_when_all_cancelled() -> None:
    view = _view(cancelled=3)
    text = _text(_container(_render(view, enable_cancellation=True, final=True)))
    assert "Cancelled" in text


def test_time_taken_line_present() -> None:
    text = _text(
        _container(_render(_view(delivered=1, pending=2), enable_cancellation=False))
    )
    assert "Time taken" in text
