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
from dd.hmessage import HMessage

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


@pytest.mark.parametrize("budget", [0, -5, 5, 10])
def test_cap_cv2_text_never_exceeds_a_tiny_budget(budget):
    # When there's no room for the ~18-unit truncation note, the note must be dropped
    # rather than returned whole (which would itself blow the budget).
    capped = components.cap_cv2_text("x" * 500, budget=budget)
    assert components.cv2_utf16_len(capped) <= max(budget, 0)


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


# --- guard_cv2_hmessage (naive truncate + CRITICAL alert on an assembled HMessage) --


def _cv2_hmsg(text: str) -> HMessage:
    container = h.impl.ContainerComponentBuilder()
    container.add_text_display(text)
    comps: list[h.api.ComponentBuilder] = [container]
    return HMessage(components=comps)


@pytest.mark.asyncio
async def test_guard_cv2_hmessage_truncates_and_alerts(monkeypatch):
    calls = _record_alerts(monkeypatch)
    hmsg = _cv2_hmsg("y" * 8000)
    out = await components.guard_cv2_hmessage(hmsg, post_name="Xûr")
    assert out is hmsg
    assert components.cv2_text_length(hmsg.components) <= components.CV2_TEXT_BUDGET
    assert calls == [("Xûr autopost", logging.CRITICAL)]


@pytest.mark.asyncio
async def test_guard_cv2_hmessage_silent_within_budget(monkeypatch):
    calls = _record_alerts(monkeypatch)
    hmsg = _cv2_hmsg("small body")
    before = list(hmsg.components)
    await components.guard_cv2_hmessage(hmsg, post_name="Xûr")
    assert hmsg.components == before  # untouched
    assert calls == []


# --- fit_cv2_components (whole-page cap on assembled builders) --------------------


def _container(*texts: str) -> h.impl.ContainerComponentBuilder:
    c = h.impl.ContainerComponentBuilder()
    for i, text in enumerate(texts):
        if i:
            c.add_separator(divider=True)
        c.add_text_display(text)
    return c


def test_fit_cv2_components_passes_through_when_within_budget():
    comps = [_container("a", "b")]
    assert components.fit_cv2_components(comps, budget=100) is not comps  # a new list
    out = components.fit_cv2_components(comps, budget=100)
    assert out[0] is comps[0]  # under budget -> builders reused untouched


def test_fit_cv2_components_trims_a_single_oversized_container():
    out = components.fit_cv2_components([_container("x" * 5000)], budget=100)
    assert components.cv2_text_length(out) <= 100


def test_fit_cv2_components_caps_the_stacked_aggregate():
    # Two native containers that each fit alone but overflow together (the navigator
    # accumulate case) — the whole page must still come in under budget.
    out = components.fit_cv2_components(
        [_container("a" * 80), _container("b" * 80)], budget=100
    )
    assert components.cv2_text_length(out) <= 100
    # Front content is kept whole; the tail is what gets trimmed.
    first = out[0]
    assert isinstance(first, h.impl.ContainerComponentBuilder)
    head = first.components[0]
    assert isinstance(head, h.impl.TextDisplayComponentBuilder)
    assert head.content == "a" * 80


def test_fit_cv2_components_preserves_non_text_and_drops_emptied_displays():
    container = h.impl.ContainerComponentBuilder()
    container.add_text_display("k" * 200)
    container.add_component(components.url_media_gallery("https://x/y.gif"))
    (out_container,) = components.fit_cv2_components([container], budget=50)
    assert isinstance(out_container, h.impl.ContainerComponentBuilder)
    kinds = [type(c).__name__ for c in out_container.components]
    # The media gallery survives even though the text before it was trimmed.
    assert "MediaGalleryComponentBuilder" in kinds
    assert components.cv2_text_length([out_container]) <= 50


# --- guard_cv2_post_sections (reserve header/footer, truncate body) --------------


@pytest.mark.asyncio
async def test_guard_cv2_post_sections_keeps_header_and_footer(monkeypatch):
    calls = _record_alerts(monkeypatch)
    header, footer = "HEADER\n", "\nFOOTER"
    out = await components.guard_cv2_post_sections(
        header, "b" * 8000, footer, post_name="Lost Sector"
    )
    assert out.startswith(header)
    assert out.endswith(footer)  # footer survives the overflow, not tail-cut
    assert "truncated" in out
    assert components.cv2_text_length([components.text_display(out)]) <= (
        components.CV2_TEXT_BUDGET
    )
    assert calls == [("Lost Sector autopost", logging.CRITICAL)]


@pytest.mark.asyncio
async def test_guard_cv2_post_sections_silent_within_budget(monkeypatch):
    calls = _record_alerts(monkeypatch)
    out = await components.guard_cv2_post_sections(
        "H", "body", "F", post_name="Lost Sector"
    )
    assert out == "HbodyF"
    assert calls == []


# --- warn_cv2_post_over_limit (built-container backstop) --------------------------


@pytest.mark.asyncio
async def test_warn_cv2_post_over_limit_alerts_over_hard_limit(monkeypatch):
    calls = _record_alerts(monkeypatch)
    container = h.impl.ContainerComponentBuilder()
    container.add_text_display("z" * 5000)  # over CV2_TEXT_LIMIT (4000)
    await components.warn_cv2_post_over_limit([container], post_name="Eververse")
    assert calls == [("Eververse autopost", logging.CRITICAL)]


@pytest.mark.asyncio
async def test_warn_cv2_post_over_limit_silent_within_limit(monkeypatch):
    calls = _record_alerts(monkeypatch)
    container = h.impl.ContainerComponentBuilder()
    container.add_text_display("a small post")
    await components.warn_cv2_post_over_limit([container], post_name="Eververse")
    assert calls == []
