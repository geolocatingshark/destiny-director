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

# Tests for the shared CV2 status responses (cv2_error / cv2_success / cv2_notice) — the
# single source of truth for the short error/success/notice containers every extension
# shows its invoker. Pure builders, no Discord I/O: assert accent colour + text content.

import hikari as h

from dd.common import components


def _texts(container: h.impl.ContainerComponentBuilder) -> list[str]:
    out: list[str] = []
    for child in container.components:
        assert isinstance(child, h.impl.TextDisplayComponentBuilder)
        out.append(child.content)
    return out


def test_cv2_error_title_only() -> None:
    container = components.cv2_error("Nope")
    assert container.accent_color == components.CV2_DANGER_COLOR
    assert _texts(container) == ["⚠️ **Nope**"]


def test_cv2_error_title_and_body_share_one_display() -> None:
    container = components.cv2_error("Bad request", "Try again later.")
    assert container.accent_color == components.CV2_DANGER_COLOR
    # Title and body live in one text display (no divider) so short errors stay compact.
    assert _texts(container) == ["⚠️ **Bad request**\nTry again later."]


def test_cv2_success_prefixes_check_mark() -> None:
    container = components.cv2_success("Announced")
    assert container.accent_color == components.CV2_SUCCESS_COLOR
    assert _texts(container) == ["✅ Announced"]


def test_cv2_notice_is_neutral_and_unmarked() -> None:
    container = components.cv2_notice("Announcing…")
    assert container.accent_color == components.CV2_NEUTRAL_COLOR
    assert _texts(container) == ["Announcing…"]
