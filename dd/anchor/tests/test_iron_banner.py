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

"""Unit tests for the Iron Banner anchor producer (no Discord I/O).

Exercises the shared manifest weapon-line resolver (light.gg links + weapon-type emoji
fallback), the Components-V2 ``format_post`` assembly (body text + the guide link
button), the posted-guard round-trip, and the rotation-editor wiring (default doc,
domain build gate, and the Discord-post preview). The manifest pool and guild emoji are
monkeypatched so nothing hits Bungie/Discord.
"""

import datetime as dt
import typing as t

import hikari as h
import pytest

from dd.anchor import hybrid_post_core as hpc
from dd.anchor.extensions import (
    iron_banner as ibx,
    rotation_editor as red,
)
from dd.common import iron_banner as ib

# A stand-in bot: ``format_post`` only passes it to the (monkeypatched) emoji fetch, so
# its concrete type is irrelevant here — typed ``Any`` to satisfy the checker.
_BOT: t.Any = object()

# A manifest-style pool: (name, hash, itemTypeDisplayName, itemType, rarity).
_POOL: list[hpc.WeaponItem] = [
    ("The Forward Path", 999, "Auto Rifle", 3, "Legendary"),
    ("Felwinter's Lie", 888, "Shotgun", 3, "Legendary"),
]

_DOC: dict[str, t.Any] = {
    "version": 1,
    "schedule": [{"start": "2026-06-30", "pool": "Pool 1", "modes": "Control / Rift"}],
    "pools": [
        {"name": "Pool 1", "weapons": ["The Forward Path (Auto Rifle)", "Unknown Gun"]}
    ],
}

# A far-future event so ``current_or_next`` deterministically returns it regardless of
# the real wall-clock the (un-injectable) ``format_post`` reads.
_FUTURE_DOC: dict[str, t.Any] = {
    "version": 1,
    "schedule": [{"start": "2099-06-30", "pool": "Pool 1", "modes": "Control / Rift"}],
    "pools": _DOC["pools"],
}


def _text(hmsg: t.Any) -> str:
    """Concatenate the text-display contents of a CV2 HMessage's single container."""
    container = hmsg.components[0]
    return "\n".join(
        c.content
        for c in container.components
        if isinstance(c, h.impl.TextDisplayComponentBuilder)
    )


def _link_buttons(hmsg: t.Any) -> list[h.impl.LinkButtonBuilder]:
    container = hmsg.components[0]
    return [
        btn
        for row in container.components
        if isinstance(row, h.impl.MessageActionRowBuilder)
        for btn in row.components
        if isinstance(btn, h.impl.LinkButtonBuilder)
    ]


# --- resolve_weapon_lines (shared helper) ----------------------------------------


@pytest.mark.asyncio
async def test_resolve_weapon_lines_links_and_emoji(monkeypatch) -> None:
    async def fake_pool() -> list[hpc.WeaponItem]:
        return _POOL

    monkeypatch.setattr(hpc, "get_weapon_pool", fake_pool)
    # Guild has the auto_rifle emoji but not shotgun -> shotgun falls back to :weapon:.
    available = {"auto_rifle", "weapon"}
    lines = await hpc.resolve_weapon_lines(
        ["The Forward Path (Auto Rifle)", "Felwinter's Lie (Shotgun)"], available
    )
    assert lines == [
        ":auto_rifle: [The Forward Path](https://light.gg/db/items/999)",
        ":weapon: [Felwinter's Lie](https://light.gg/db/items/888)",
    ]


@pytest.mark.asyncio
async def test_resolve_weapon_lines_plain_name_when_unresolved(monkeypatch) -> None:
    async def fake_pool() -> list[hpc.WeaponItem]:
        return _POOL

    monkeypatch.setattr(hpc, "get_weapon_pool", fake_pool)
    # A name not in the pool has no hash -> no light.gg link, generic :weapon:.
    lines = await hpc.resolve_weapon_lines(["Unknown Gun"], {"weapon"})
    assert lines == [":weapon: Unknown Gun"]


# --- format_post -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_format_post_builds_body_and_guide_button(monkeypatch) -> None:
    async def fake_pool() -> list[hpc.WeaponItem]:
        return _POOL

    async def fake_emoji(_bot) -> dict[str, h.Emoji]:
        return {}

    monkeypatch.setattr(hpc, "get_weapon_pool", fake_pool)
    monkeypatch.setattr(ibx, "fetch_emoji_dict", fake_emoji)
    monkeypatch.setattr(
        ib,
        "load_rotation",
        lambda: _rotation_coro(ib.IronBannerRotation.from_json(_FUTURE_DOC)),
    )

    hmsg = await ibx.format_post(_BOT)
    body = _text(hmsg)
    assert "# [Iron Banner]" in body
    assert "### Game Modes" in body and "- Control" in body and "- Rift" in body
    assert "### Bonus Focus Pool" in body
    # Resolved weapon keeps its light.gg link; the unresolved one is a plain name.
    assert "[The Forward Path](https://light.gg/db/items/999)" in body
    assert "Unknown Gun" in body

    # The standard footer row: Iron Banner Guide + Support.
    urls = [b.url for b in _link_buttons(hmsg)]
    assert urls == [ib.GUIDE_URL, "https://ko-fi.com/Kyber3000"]


@pytest.mark.asyncio
async def test_format_post_raises_without_event(monkeypatch) -> None:
    empty = ib.IronBannerRotation.from_json({"version": 1, "schedule": [], "pools": []})
    monkeypatch.setattr(ib, "load_rotation", lambda: _rotation_coro(empty))
    with pytest.raises(RuntimeError, match="No current or upcoming"):
        await ibx.format_post(_BOT)


async def _rotation_coro(rotation: ib.IronBannerRotation) -> ib.IronBannerRotation:
    return rotation


# --- posted-guard round-trip -----------------------------------------------------


@pytest.mark.asyncio
async def test_posted_guard_round_trip() -> None:
    assert await ibx._load_last_posted_reset() == 0  # absent -> 0
    await ibx._save_last_posted_reset(1782838800)
    assert await ibx._load_last_posted_reset() == 1782838800


def test_event_period_normalises_to_reset_week() -> None:
    # A Tuesday-17:00 start is itself a reset boundary; a later day in the SAME reset
    # week collapses to the same period key — so correcting an event's start date within
    # its week won't look like a new event and re-trigger the post.
    tue = int(dt.datetime(2026, 6, 30, 17, tzinfo=dt.UTC).timestamp())
    thu = int(dt.datetime(2026, 7, 2, 17, tzinfo=dt.UTC).timestamp())
    ev_tue = ib.Event(tue, tue + 7 * 86400, "Pool 1", ["Control"], [])
    ev_thu = ib.Event(thu, thu + 7 * 86400, "Pool 1", ["Control"], [])
    assert ibx._event_period(ev_tue) == tue
    assert ibx._event_period(ev_thu) == tue


# --- rotation-editor wiring -------------------------------------------------------


def test_editor_default_doc_is_the_seed() -> None:
    doc = red._default_doc("iron_banner")
    assert [p["name"] for p in doc["pools"]] == ["Pool 1", "Pool 2"]
    assert doc["schedule"]  # non-empty seeded schedule


def test_editor_build_domain_object_gate() -> None:
    obj = red._build_domain_object("iron_banner", _DOC)
    assert isinstance(obj, ib.IronBannerRotation)
    with pytest.raises(ValueError):
        red._build_domain_object(
            "iron_banner",
            {"pools": [], "schedule": [{"start": "2026-06-30", "pool": "Nope"}]},
        )


@pytest.mark.asyncio
async def test_editor_preview_renders_the_post(monkeypatch) -> None:
    async def fake_pool() -> list[hpc.WeaponItem]:
        return _POOL

    monkeypatch.setattr(hpc, "get_weapon_pool", fake_pool)
    rotation = ib.IronBannerRotation.from_json(_DOC)
    html = await red._render_iron_banner_preview(rotation, {})
    # The preview is the real Discord post wall: it carries the event label + the
    # resolved bonus-pool weapon (light.gg linked).
    assert "Pool 1" in html
    assert "light.gg/db/items/999" in html
