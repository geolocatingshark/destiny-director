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
from unittest.mock import MagicMock

import hikari as h

from dd.beacon.extensions.mirror import (
    _CANCEL_CUSTOM_ID_PREFIX,
    flag_mirror_failure_ratio,
    render_mirror_progress,
)
from dd.beacon.mirror_core import MirrorOperationType, RunFailure, RunView
from dd.common import cfg
from dd.common.utils import ErrorClass


def _view(op=MirrorOperationType.SEND, source_message_id=832, total=3):
    return RunView(
        op=op,
        src_ch_id=1,
        src_msg_id=source_message_id,
        total=total,
        start_time=perf_counter(),
    )


def _fail(view, ch, ref="PERM01", cls=ErrorClass.PERMANENT):
    view.on_failed(ch, RunFailure(ref, cls, "boom"))


def _render(view, *, enable_cancellation, final=False):
    return render_mirror_progress(
        view,
        title="Mirror send progress",
        source_message_link="https://discord.com/x",
        source_message_summary="Announcement",
        source_channel_link="https://discord.com/y",
        source_channel_name="news",
        final=final,
        enable_cancellation=enable_cancellation,
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
    view = _view()
    view.on_delivered(10)
    container = _container(_render(view, enable_cancellation=False))
    assert container.accent_color == cfg.embed_default_color


def test_error_accent_when_failures() -> None:
    view = _view()
    _fail(view, 10)
    container = _container(_render(view, enable_cancellation=False))
    assert container.accent_color == cfg.embed_error_color
    assert "PERM01" in _text(container)  # the failure breakdown surfaces the ref code


def test_cancel_row_present_for_send_in_progress() -> None:
    view = _view(op=MirrorOperationType.SEND, source_message_id=832)
    container = _container(_render(view, enable_cancellation=True, final=False))
    assert _cancel_custom_ids(container) == [f"{_CANCEL_CUSTOM_ID_PREFIX}:832"]


def test_cancel_row_namespaced_by_source_message_id() -> None:
    a = _container(_render(_view(source_message_id=111), enable_cancellation=True))
    b = _container(_render(_view(source_message_id=222), enable_cancellation=True))
    assert _cancel_custom_ids(a) != _cancel_custom_ids(b)


def test_no_cancel_row_when_final() -> None:
    container = _container(_render(_view(), enable_cancellation=True, final=True))
    assert _cancel_custom_ids(container) == []


def test_no_cancel_row_when_disabled() -> None:
    # DELETE never enables cancellation.
    view = _view(op=MirrorOperationType.DELETE)
    container = _container(_render(view, enable_cancellation=False))
    assert _cancel_custom_ids(container) == []


def test_cancel_row_dropped_once_cancellation_requested() -> None:
    # Once cancel fires (cancel_requested set) the button is removed even though work is
    # still draining, so it can't be pressed twice.
    view = _view()
    view.cancel_requested = True
    container = _container(_render(view, enable_cancellation=True, final=False))
    assert _cancel_custom_ids(container) == []
    assert "Cancelling" in _text(container)


def test_disabled_count_line_on_final_render() -> None:
    # The disabled-channel count appears only on the final render, and only when the
    # run-end sweep actually disabled something.
    view = _view()
    view.on_delivered(10)
    view.disabled_count = 3
    assert "Disabled channels: 3" not in _text(
        _container(_render(view, enable_cancellation=False))
    )
    final = _container(_render(view, enable_cancellation=False, final=True))
    assert "Disabled channels: 3" in _text(final)


def test_no_disabled_count_line_when_none_disabled() -> None:
    view = _view()
    view.on_delivered(10)
    final = _container(_render(view, enable_cancellation=False, final=True))
    assert "Disabled channels" not in _text(final)


def test_no_first_pass_line() -> None:
    # The "Time to try all channels once" line was dropped; "Time taken" stays.
    view = _view()
    text = _text(_container(_render(view, enable_cancellation=False)))
    assert "Time taken" in text
    assert "Time to try all channels once" not in text


# -- flag_mirror_failure_ratio ----------------------------------------------


def test_flag_critical_on_majority_failure(monkeypatch) -> None:
    logger = MagicMock()
    monkeypatch.setattr("dd.beacon.extensions.mirror.health_logger", logger)
    view = _view(total=10)
    for ch in range(5):
        _fail(view, ch, cls=ErrorClass.TRANSIENT)  # 5/10 failed → majority
    flag_mirror_failure_ratio(view)
    logger.critical.assert_called_once()
    logger.error.assert_not_called()


def test_flag_error_on_permanent_minority(monkeypatch) -> None:
    logger = MagicMock()
    monkeypatch.setattr("dd.beacon.extensions.mirror.health_logger", logger)
    view = _view(total=3)
    _fail(view, 0, cls=ErrorClass.PERMANENT)  # below sample size, but permanent
    flag_mirror_failure_ratio(view)
    logger.error.assert_called_once()
    logger.critical.assert_not_called()


def test_flag_silent_when_no_failures(monkeypatch) -> None:
    logger = MagicMock()
    monkeypatch.setattr("dd.beacon.extensions.mirror.health_logger", logger)
    view = _view(total=3)
    view.on_delivered(0)
    flag_mirror_failure_ratio(view)
    logger.critical.assert_not_called()
    logger.error.assert_not_called()
