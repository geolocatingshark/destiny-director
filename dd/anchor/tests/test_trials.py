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

"""Unit tests for the ``trials`` extension's pure logic (no Discord I/O).

Exercises the ``Live until`` reset maths, the exact post-body renderer,
(de)serialisation, the carried-over draft build, validation, the server-side
``_context_from_payload`` (maps split + focus-pool resolution), the shared
publish/route path wired through this producer's spec, and the preview renderer's H3 /
bullet handling. Auth is enforced centrally by the web_auth middleware (see
test_web_auth.py), so there are no session tests here.
"""

import json
import re
import types
import typing as t

import aiohttp.web
import hikari as h
import pytest

from dd.anchor import hybrid_post_core as hpc
from dd.anchor.extensions import trials as tr

# ---------------------------------------------------------------------------
# build_body + "Live until" reset maths
# ---------------------------------------------------------------------------

# A known Tuesday 17:00 UTC boundary (the same convention weekly_reset is anchored to).
SAMPLE_RESET = 1783702800


def test_live_until_is_next_reset() -> None:
    ctx = tr.TrialsContext(reset_ts=SAMPLE_RESET)
    body = tr.build_body(ctx)
    assert f"Live until <t:{hpc.next_reset_ts(SAMPLE_RESET)}:f>" in body


def test_build_body_exact_format() -> None:
    ctx = tr.TrialsContext(
        reset_ts=SAMPLE_RESET,
        featured_maps=["Burnout", "Widow's Court", "Endless Vale"],
        focus_pool=[tr.WeaponRef("The Scholar", 123), tr.WeaponRef("Exile's Curse")],
    )
    lines = tr.build_body(ctx).split("\n")
    assert lines[0] == "# [Trials *of* Osiris](https://kyber3000.com/Trialspost)"
    assert lines[1] == ""
    assert lines[2] == f"Live until <t:{hpc.next_reset_ts(SAMPLE_RESET)}:f>"
    assert lines[3] == "### Featured Maps"
    assert "- Burnout" in lines and "- Widow's Court" in lines
    assert "### Rewards" in lines
    assert "All Trials weapons available" in lines
    assert "Weapon Attunement available" in lines
    assert "**This Week's Bonus Focus Pool**" in lines
    # Manifest-linked weapon -> light.gg deep link; hash-less weapon -> plain name.
    assert "- [The Scholar](https://light.gg/db/items/123)" in lines
    assert "- Exile's Curse" in lines
    assert lines[-1] == "### Good luck in your games!  :gscheer:"


def test_build_body_hides_empty_optional_sections() -> None:
    # No maps -> no Featured Maps header; no focus pool -> no Focus Pool header. Rewards
    # (static) and the footer are always present.
    only_maps = tr.build_body(tr.TrialsContext(reset_ts=1, featured_maps=["Burnout"]))
    assert "### Featured Maps" in only_maps
    assert "Bonus Focus Pool" not in only_maps

    only_pool = tr.build_body(
        tr.TrialsContext(reset_ts=1, focus_pool=[tr.WeaponRef("The Scholar")])
    )
    assert "### Featured Maps" not in only_pool
    assert "**This Week's Bonus Focus Pool**" in only_pool

    both_empty = tr.build_body(tr.TrialsContext(reset_ts=1))
    assert "### Rewards" in both_empty
    assert both_empty.rstrip().endswith("### Good luck in your games!  :gscheer:")


# ---------------------------------------------------------------------------
# (de)serialisation + carried-over draft
# ---------------------------------------------------------------------------


def test_context_round_trip() -> None:
    ctx = tr.TrialsContext(
        reset_ts=SAMPLE_RESET,
        featured_maps=["A", "B"],
        focus_pool=[tr.WeaponRef("W", 1, "scout_rifle"), tr.WeaponRef("V")],
        image_url="https://x/y.png",
        notes=["n1"],
    )
    assert tr.TrialsContext.from_dict(ctx.to_dict()) == ctx


def test_config_round_trip_and_default_seeds_loot_sets() -> None:
    # A blank config seeds the baked loot-set loop and an unused cursor.
    fresh = tr.TrialsConfig.from_dict(None)
    assert fresh.loot_sets == [list(s) for s in tr.DEFAULT_LOOT_SETS]
    assert fresh.last_loot_set_index == -1
    config = tr.TrialsConfig(
        default_image_url="https://img",
        last_featured_maps=["Burnout"],
        loot_sets=[["A", "B"], ["C"]],
        last_loot_set_index=1,
    )
    assert tr.TrialsConfig.from_dict(config.to_dict()) == config


def test_next_loot_set_loops_and_match() -> None:
    config = tr.TrialsConfig(loot_sets=[["A", "B"], ["C", "D"], ["E"]])
    config.last_loot_set_index = -1
    assert config.next_loot_set() == ["A", "B"]  # first draft -> set 0
    config.last_loot_set_index = 0
    assert config.next_loot_set() == ["C", "D"]
    config.last_loot_set_index = 2
    assert config.next_loot_set() == ["A", "B"]  # wraps
    # match is order-insensitive + case-insensitive; a non-set returns None.
    assert config.match_loot_set(["d", "c"]) == 1
    assert config.match_loot_set(["A", "X"]) is None


@pytest.mark.asyncio
async def test_build_draft_context_defaults_to_next_loot_set(stub_weapon_items) -> None:
    # last used = set 0 (Pool 1) -> the draft defaults to set 1 (Pool 2), linked.
    config = tr.TrialsConfig(
        default_image_url="https://img",
        last_featured_maps=["Burnout"],
        last_loot_set_index=0,
    )
    ctx = await tr.build_draft_context(config)
    assert ctx.reset_ts == hpc.current_reset_ts()
    assert ctx.featured_maps == ["Burnout"]
    assert [w.name for w in ctx.focus_pool] == list(tr.DEFAULT_LOOT_SETS[1])
    assert ctx.image_url == "https://img"


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def test_validate_flags_empty_post() -> None:
    problems = tr.validate_post(tr.TrialsContext(reset_ts=1))
    assert any("empty" in p for p in problems)


def test_validate_ok_with_a_single_map() -> None:
    assert (
        tr.validate_post(tr.TrialsContext(reset_ts=1, featured_maps=["Burnout"])) == []
    )


def test_validate_rejects_bad_image_url() -> None:
    problems = tr.validate_post(
        tr.TrialsContext(reset_ts=1, featured_maps=["X"], image_url="not-a-url")
    )
    assert any("http" in p for p in problems)


def test_validate_flags_overlong_post() -> None:
    ctx = tr.TrialsContext(reset_ts=1, featured_maps=["x" * 5000])
    assert any("too long" in p for p in tr.validate_post(ctx))


# ---------------------------------------------------------------------------
# server-side context from the form payload
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_weapon_items():
    saved = tr._weapon_items
    tr._weapon_items = [
        ("The Scholar", 123, "Scout Rifle", 3, "Legendary"),
        ("Exile's Curse", 456, "Fusion Rifle", 3, "Legendary"),
    ]
    yield
    tr._weapon_items = saved


@pytest.mark.asyncio
async def test_context_from_payload_splits_and_resolves(stub_weapon_items) -> None:
    ctx = await tr._context_from_payload(
        {
            "reset_ts": SAMPLE_RESET,
            "maps_text": "Burnout\n  Widow's Court \n\n",
            "focus_pool": ["123", "Sola's Scar", "456"],
            "image_url": "  https://img/y.png  ",
            "notes_text": "note1\n\n",
        }
    )
    assert ctx.reset_ts == SAMPLE_RESET
    assert ctx.featured_maps == ["Burnout", "Widow's Court"]
    # by hash -> linked; free text -> hash-less; by hash -> linked.
    assert ctx.focus_pool[0].name == "The Scholar" and ctx.focus_pool[0].hash == 123
    assert ctx.focus_pool[1].name == "Sola's Scar" and ctx.focus_pool[1].hash is None
    assert ctx.focus_pool[2].name == "Exile's Curse" and ctx.focus_pool[2].hash == 456
    assert ctx.image_url == "https://img/y.png"
    assert ctx.notes == ["note1"]


@pytest.mark.asyncio
async def test_context_from_payload_defaults_reset(stub_weapon_items) -> None:
    ctx = await tr._context_from_payload({"maps_text": "Burnout"})
    assert ctx.reset_ts == hpc.current_reset_ts()


# ---------------------------------------------------------------------------
# publish / route lifecycle (fake bot + fake request)
# ---------------------------------------------------------------------------


class _FakeRest:
    def __init__(self) -> None:
        self.edited: list[tuple[t.Any, int]] = []
        self.deleted: list[tuple[t.Any, int]] = []

    async def edit_message(self, channel: t.Any, message: int, **kwargs: t.Any) -> None:
        self.edited.append((channel, message))

    async def delete_message(self, channel: t.Any, message: int) -> None:
        self.deleted.append((channel, message))


class _FakeBot:
    def __init__(self) -> None:
        self.rest = _FakeRest()


def _bot(fake: _FakeBot) -> hpc.CachedFetchBot:
    return t.cast(hpc.CachedFetchBot, fake)


@pytest.fixture
def fake_publish_env(monkeypatch: pytest.MonkeyPatch):
    """Stub render + send/crosspost so the shared publish branches are testable.

    ``format_trials`` (late-bound by the spec) returns a dummy bundle; the ``utils``
    send/crosspost primitives — shared with the core via one module object — record
    calls instead of hitting Discord.
    """
    sent: list[dict[str, t.Any]] = []
    crossposted: list[tuple[t.Any, int]] = []

    async def fake_format(ctx: t.Any, bot: t.Any) -> t.Any:
        return types.SimpleNamespace(components=["cv2"])

    async def fake_send(
        bot: t.Any,
        msg_proto: t.Any,
        channel_id: int,
        crosspost: bool = True,
        deduplicate: bool = False,
    ) -> t.Any:
        sent.append({"channel": channel_id, "crosspost": crosspost})
        return types.SimpleNamespace(id=555)

    async def fake_crosspost(bot: t.Any, channel: t.Any, message_id: int) -> None:
        crossposted.append((channel, message_id))

    monkeypatch.setattr(tr, "format_trials", fake_format)
    monkeypatch.setattr(hpc.utils, "send_message", fake_send)
    monkeypatch.setattr(hpc.utils, "crosspost_message_with_retries", fake_crosspost)
    return types.SimpleNamespace(sent=sent, crossposted=crossposted)


class _FakeRequest:
    def __init__(self, *, body: t.Any = None) -> None:
        self.query: dict[str, str] = {}
        self.cookies: dict[str, str] = {}
        self.headers: dict[str, str] = {}
        self._body = body

    async def json(self) -> t.Any:
        return self._body


def _req(**kwargs: t.Any) -> aiohttp.web.Request:
    return t.cast(aiohttp.web.Request, _FakeRequest(**kwargs))


def _ctx() -> tr.TrialsContext:
    return tr.TrialsContext(reset_ts=SAMPLE_RESET, featured_maps=["Burnout"])


@pytest.mark.asyncio
async def test_post_or_edit_unpublished_creates_then_edits(fake_publish_env) -> None:
    bot = _FakeBot()
    channel = tr.cfg.followables["trials"]
    meta = await hpc.post_or_edit_unpublished(
        tr._SPEC, _bot(bot), _ctx(), tr.DraftMeta()
    )
    assert fake_publish_env.sent == [{"channel": channel, "crosspost": False}]
    assert meta.message_id == 555 and meta.status == "posted"

    await hpc.post_or_edit_unpublished(
        tr._SPEC, _bot(bot), _ctx(), tr.DraftMeta(message_id=42, status="posted")
    )
    assert bot.rest.edited == [(channel, 42)]


@pytest.mark.asyncio
async def test_publish_draft_edits_then_crossposts(fake_publish_env) -> None:
    bot = _FakeBot()
    channel = tr.cfg.followables["trials"]
    meta = tr.DraftMeta(message_id=42, status="posted", crossposted=False)
    out, note = await hpc.publish_draft(tr._SPEC, _bot(bot), _ctx(), meta)
    assert bot.rest.edited == [(channel, 42)]
    assert fake_publish_env.crossposted == [(channel, 42)]
    assert out.crossposted is True and out.status == "published"
    assert "Published and crossposted" in note


@pytest.mark.asyncio
async def test_publish_draft_raises_on_invalid(fake_publish_env) -> None:
    bot = _FakeBot()
    with pytest.raises(ValueError):
        await hpc.publish_draft(
            tr._SPEC, _bot(bot), tr.TrialsContext(reset_ts=1), tr.DraftMeta()
        )
    assert fake_publish_env.sent == [] and fake_publish_env.crossposted == []


@pytest.mark.asyncio
async def test_handle_create_posts_and_returns_warnings(
    monkeypatch, fake_publish_env, stub_weapon_items
) -> None:
    monkeypatch.setattr(tr, "_bot", _FakeBot())
    await tr.save_meta(tr.DraftMeta())  # fresh: no post this period
    # An empty draft trips validate_post, but Create still posts it — the problems come
    # back as non-blocking warnings, not a 422; the post is stamped as this period's.
    resp = await tr._handle_create(_req(body={"reset_ts": SAMPLE_RESET}))
    assert resp.status == 200
    data = json.loads(resp.text or "")
    assert data["ok"] is True and data["warnings"]
    assert data["post_this_period"] is True and data["crossposted"] is False
    channel = tr.cfg.followables["trials"]
    assert fake_publish_env.sent == [{"channel": channel, "crosspost": False}]
    meta = await tr.load_meta()
    assert meta.message_id == 555 and meta.status == "posted"
    assert meta.reset_ts == tr.current_reset_ts()  # stamped to the real period


@pytest.mark.asyncio
async def test_handle_create_refuses_when_post_exists(
    monkeypatch, stub_weapon_items
) -> None:
    monkeypatch.setattr(tr, "_bot", _FakeBot())
    # A legacy-stamped (reset_ts=0) live post is always "current" — Create is refused.
    await tr.save_meta(tr.DraftMeta(message_id=42, reset_ts=0, status="posted"))
    resp = await tr._handle_create(_req(body={"reset_ts": SAMPLE_RESET}))
    assert resp.status == 409
    assert "already exists" in json.loads(resp.text or "")["error"]


@pytest.mark.asyncio
async def test_handle_create_publish_crossposts(
    monkeypatch, fake_publish_env, stub_weapon_items
) -> None:
    monkeypatch.setattr(tr, "_bot", _FakeBot())
    await tr.save_meta(tr.DraftMeta())
    resp = await tr._handle_create(
        _req(body={"reset_ts": SAMPLE_RESET, "maps_text": "Burnout", "publish": True})
    )
    assert resp.status == 200 and json.loads(resp.text or "")["crossposted"] is True
    channel = tr.cfg.followables["trials"]
    assert fake_publish_env.crossposted == [(channel, 555)]


@pytest.mark.asyncio
async def test_handle_edit_edits_existing_in_place(
    monkeypatch, fake_publish_env, stub_weapon_items
) -> None:
    bot = _FakeBot()
    monkeypatch.setattr(tr, "_bot", bot)
    await tr.save_meta(tr.DraftMeta(message_id=42, reset_ts=0, status="posted"))
    resp = await tr._handle_edit(
        _req(body={"reset_ts": SAMPLE_RESET, "maps_text": "Burnout"})
    )
    assert resp.status == 200
    data = json.loads(resp.text or "")
    assert data["ok"] is True and data["post_this_period"] is True
    channel = tr.cfg.followables["trials"]
    assert bot.rest.edited == [(channel, 42)] and fake_publish_env.sent == []


@pytest.mark.asyncio
async def test_handle_edit_refuses_when_absent(monkeypatch, stub_weapon_items) -> None:
    monkeypatch.setattr(tr, "_bot", _FakeBot())
    await tr.save_meta(tr.DraftMeta())  # no post this period
    resp = await tr._handle_edit(_req(body={"reset_ts": SAMPLE_RESET}))
    assert resp.status == 409
    assert "No Trials post" in json.loads(resp.text or "")["error"]


@pytest.mark.asyncio
async def test_handle_create_carries_maps_and_advances_loot_cursor(
    monkeypatch, fake_publish_env, stub_weapon_items
) -> None:
    monkeypatch.setattr(tr, "_bot", _FakeBot())
    await tr.save_config(tr.TrialsConfig())  # reset the shared-DB rotation state
    await tr.save_meta(tr.DraftMeta())  # cursor starts unused (-1)
    # Commit a post whose focus pool is exactly Pool 2 (index 1) — the cursor advances
    # to that set so the next draft defaults to Pool 3.
    await tr._handle_create(
        _req(
            body={
                "reset_ts": SAMPLE_RESET,
                "maps_text": "Burnout",
                "focus_pool": list(tr.DEFAULT_LOOT_SETS[1]),
            }
        )
    )
    config = await tr.load_config()
    assert config.last_featured_maps == ["Burnout"]
    assert config.last_loot_set_index == 1
    assert config.next_loot_set() == list(tr.DEFAULT_LOOT_SETS[2])


@pytest.mark.asyncio
async def test_handle_create_custom_pool_leaves_cursor(
    monkeypatch, fake_publish_env, stub_weapon_items
) -> None:
    monkeypatch.setattr(tr, "_bot", _FakeBot())
    await tr.save_config(tr.TrialsConfig())  # reset the shared-DB rotation state (-1)
    await tr.save_meta(tr.DraftMeta())
    # A focus pool that matches no known set leaves the rotation cursor untouched.
    await tr._handle_create(
        _req(body={"reset_ts": SAMPLE_RESET, "focus_pool": ["The Scholar"]})
    )
    assert (await tr.load_config()).last_loot_set_index == -1


@pytest.mark.asyncio
async def test_handle_create_503_when_bot_unset(monkeypatch) -> None:
    monkeypatch.setattr(tr, "_bot", None)
    resp = await tr._handle_create(_req(body={"reset_ts": 1}))
    assert resp.status == 503


@pytest.mark.asyncio
async def test_handle_delete_removes_and_clears_reset_ts(monkeypatch) -> None:
    bot = _FakeBot()
    monkeypatch.setattr(tr, "_bot", bot)
    await tr.save_meta(
        tr.DraftMeta(
            message_id=77, reset_ts=SAMPLE_RESET, status="published", crossposted=True
        )
    )
    resp = await tr._handle_delete(_req())
    assert resp.status == 200 and json.loads(resp.text or "") == {"ok": True}
    assert bot.rest.deleted == [(tr.cfg.followables["trials"], 77)]
    meta = await tr.load_meta()
    assert meta.message_id == 0 and meta.reset_ts == 0
    assert meta.crossposted is False and meta.status == "draft"


@pytest.mark.asyncio
async def test_handle_auto_round_trips(monkeypatch) -> None:
    resp = await tr._handle_auto(_req(body={"enabled": True}))
    assert json.loads(resp.text or "") == {"enabled": True}
    assert await tr.schemas.AutoPostSettings.get_trials_enabled() is True
    resp = await tr._handle_auto(_req(body={"enabled": False}))
    assert json.loads(resp.text or "") == {"enabled": False}


# ---------------------------------------------------------------------------
# preview renderer (H3 headings + bullets, tag whitelist)
# ---------------------------------------------------------------------------


def test_preview_emits_h3_and_bullets_only_whitelisted_tags() -> None:
    ctx = tr.TrialsContext(
        reset_ts=SAMPLE_RESET,
        featured_maps=["Burnout"],
        focus_pool=[tr.WeaponRef("The Scholar", 123)],
    )
    emoji: dict = {}
    out = hpc.render_post_html(tr.build_body(ctx), t.cast("dict[str, h.Emoji]", emoji))
    # H3 headers and bullets render as their spans; the title's inner *of* italicises.
    assert '<span class="md-h3">' in out
    assert '<span class="md-bullet">' in out
    assert "<em>of</em>" in out
    # The light.gg deep link is a real anchor.
    assert '<a href="https://light.gg/db/items/123">' in out
    # ONLY the whitelisted tags are ever emitted (no <ul>/<li>/<script>).
    tags = set(re.findall(r"</?([a-zA-Z]+)", out))
    assert tags <= {"span", "strong", "em", "a", "img"}, tags
