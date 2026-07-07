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

"""Tests for the shared CV2 text-cap primitives and autopost overflow guards."""

import logging

import hikari as h
import pytest

from dd.common import components

# --- cv2_utf16_len / cap_cv2_text (pure) -----------------------------------------


def test_cv2_utf16_len_counts_astral_as_two():
    assert components.cv2_utf16_len("ab") == 2
    assert components.cv2_utf16_len("💀") == 2  # astral glyph → 2 UTF-16 units
    assert components.cv2_utf16_len("a💀b") == 4


def test_cap_cv2_text_leaves_short_text_untouched():
    assert components.cap_cv2_text("short", budget=100) == "short"


def test_cap_cv2_text_truncates_over_budget_with_note():
    capped = components.cap_cv2_text("x" * 500, budget=100)
    assert components.cv2_utf16_len(capped) <= 100
    assert "truncated" in capped


def test_cap_cv2_text_cuts_on_a_codepoint_boundary():
    # A run of astral glyphs: cutting in UTF-16 must never split a surrogate pair.
    capped = components.cap_cv2_text("💀" * 100, budget=50)
    assert components.cv2_utf16_len(capped) <= 50
    capped.encode("utf-16-le").decode("utf-16-le")  # round-trips (no lone surrogate)


# --- guard_cv2_post_text (truncate + CRITICAL alert) -----------------------------


def _record_alerts(monkeypatch) -> list:
    calls: list = []

    async def _fake(e, *a, operation=None, level=logging.ERROR, **k):
        calls.append((operation, level))
        return "REF"

    monkeypatch.setattr("dd.common.utils.discord_error_logger", _fake)
    return calls


@pytest.mark.asyncio
async def test_guard_cv2_post_text_alerts_critical_and_truncates(monkeypatch):
    calls = _record_alerts(monkeypatch)
    out = await components.guard_cv2_post_text("y" * 8000, post_name="Xûr")
    assert components.cv2_utf16_len(out) <= components.CV2_TEXT_BUDGET
    assert "truncated" in out
    assert calls == [("Xûr autopost", logging.CRITICAL)]


@pytest.mark.asyncio
async def test_guard_cv2_post_text_is_silent_within_budget(monkeypatch):
    calls = _record_alerts(monkeypatch)
    out = await components.guard_cv2_post_text("small body", post_name="Xûr")
    assert out == "small body"
    assert calls == []


# --- guard_cv2_post_length (built-container backstop) ------------------------------


@pytest.mark.asyncio
async def test_guard_cv2_post_length_alerts_over_hard_limit(monkeypatch):
    calls = _record_alerts(monkeypatch)
    container = h.impl.ContainerComponentBuilder()
    container.add_text_display("z" * 5000)  # over CV2_TEXT_LIMIT (4000)
    await components.guard_cv2_post_length([container], post_name="Eververse")
    assert calls == [("Eververse autopost", logging.CRITICAL)]


@pytest.mark.asyncio
async def test_guard_cv2_post_length_silent_within_limit(monkeypatch):
    calls = _record_alerts(monkeypatch)
    container = h.impl.ContainerComponentBuilder()
    container.add_text_display("a small post")
    await components.guard_cv2_post_length([container], post_name="Eververse")
    assert calls == []
