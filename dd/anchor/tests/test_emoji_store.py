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

# Integration tests (SQLite via conftest) for the lazy application-emoji store:
# AppEmojiCache CRUD/LRU ordering, and AppEmojiStore's upload-on-miss / cache-hit /
# icon-change-recreate / LRU-eviction / start-reconcile behaviour against a fake REST.

import asyncio
import typing as t

import hikari as h
import pytest
from sqlalchemy import delete

from dd.common import schemas
from dd.common.emoji_store import (
    AppEmojiStore,
    rewrite_item_emoji,
    rewrite_item_emoji_in_message,
)
from dd.hmessage import HMessage

pytestmark = pytest.mark.asyncio

APP = 4242


@pytest.fixture(autouse=True)
def _clean_cache() -> t.Iterator[None]:
    async def _clear() -> None:
        async with schemas.db_session() as session, session.begin():
            await session.execute(delete(schemas.AppEmojiCache))

    asyncio.run(_clear())
    yield


# --- fake hikari REST / bot -------------------------------------------------------


class _FakeEmoji:
    def __init__(self, id_: int, name: str) -> None:
        self.id = id_
        self.name = name

    def __str__(self) -> str:
        return f"<:{self.name}:{self.id}>"


class _FakeRest:
    def __init__(self, app_id: int = APP) -> None:
        self._app_id = app_id
        self._emojis: dict[int, _FakeEmoji] = {}
        self._next = 1000
        self.created: list[str] = []
        self.deleted: list[int] = []

    async def fetch_application(self) -> t.Any:
        return type("App", (), {"id": self._app_id})()

    async def fetch_application_emojis(self, _app: int) -> list[_FakeEmoji]:
        return list(self._emojis.values())

    async def create_application_emoji(
        self, _app: int, name: str, image: t.Any
    ) -> _FakeEmoji:
        # Simulate real Discord: an empty image URL (e.g. an adopted emoji with no known
        # icon_url) can't be uploaded and raises.
        if not getattr(image, "url", None):
            raise ValueError("empty image url")
        self._next += 1
        emoji = _FakeEmoji(self._next, name)
        self._emojis[self._next] = emoji
        self.created.append(name)
        return emoji

    async def delete_application_emoji(self, _app: int, emoji_id: t.Any) -> None:
        self._emojis.pop(int(emoji_id), None)
        self.deleted.append(int(emoji_id))


class _FakeBot:
    def __init__(self, rest: _FakeRest) -> None:
        self.rest = rest

    def listen(self, _event: t.Any) -> t.Callable[[t.Any], t.Any]:
        # AppEmojiStore registers a StartedEvent warm-up listener; tests call start()
        # explicitly, so the decorator is a no-op passthrough.
        return lambda fn: fn


def _store(capacity: int = 1900) -> tuple[AppEmojiStore, _FakeRest]:
    rest = _FakeRest()
    bot = t.cast(h.GatewayBot, _FakeBot(rest))
    return AppEmojiStore(bot, capacity=capacity), rest


# --- AppEmojiCache -----------------------------------------------------------------


async def test_cache_upsert_and_fetch() -> None:
    await schemas.AppEmojiCache.upsert(APP, "hand_cannon", 111, "http://icon/a.png")
    rows = await schemas.AppEmojiCache.all_for_app(APP)
    assert {r.name for r in rows} == {"hand_cannon"}
    assert rows[0].emoji_id == 111
    assert rows[0].icon_url == "http://icon/a.png"


async def test_cache_scoped_by_app() -> None:
    await schemas.AppEmojiCache.upsert(APP, "a", 1, "u")
    await schemas.AppEmojiCache.upsert(9999, "b", 2, "u")
    assert {r.name for r in await schemas.AppEmojiCache.all_for_app(APP)} == {"a"}


async def test_cache_oldest_orders_by_last_used() -> None:
    await schemas.AppEmojiCache.upsert(APP, "first", 1, "u")
    await schemas.AppEmojiCache.upsert(APP, "second", 2, "u")
    # Re-touch "first" so it is now the most-recently-used.
    await schemas.AppEmojiCache.touch(APP, ["first"])
    assert await schemas.AppEmojiCache.oldest(APP, 1) == ["second"]


async def test_cache_remove() -> None:
    await schemas.AppEmojiCache.upsert(APP, "gone", 1, "u")
    await schemas.AppEmojiCache.remove(APP, "gone")
    assert await schemas.AppEmojiCache.all_for_app(APP) == []


# --- AppEmojiStore -----------------------------------------------------------------


async def test_store_uploads_on_miss_then_caches() -> None:
    store, rest = _store()
    await store.start()

    e1 = await store.get("chroma_rush", "http://icon/chroma.png")
    assert e1 is not None and str(e1) == f"<:chroma_rush:{e1.id}>"
    assert rest.created == ["chroma_rush"]

    e2 = await store.get("chroma_rush", "http://icon/chroma.png")
    assert e2 is not None and e2.id == e1.id
    assert rest.created == ["chroma_rush"]  # cache hit: no second upload

    rows = await schemas.AppEmojiCache.all_for_app(APP)
    assert {r.name for r in rows} == {"chroma_rush"}


async def test_store_recreates_on_icon_change() -> None:
    store, rest = _store()
    await store.start()
    first = await store.get("vulpecula", "http://icon/old.png")
    second = await store.get("vulpecula", "http://icon/new.png")
    assert first is not None and second is not None
    assert second.id != first.id
    assert first.id in rest.deleted
    assert rest.created == ["vulpecula", "vulpecula"]


async def test_store_evicts_lru_at_capacity() -> None:
    store, rest = _store(capacity=2)
    await store.start()
    a = await store.get("a", "u")
    await store.get("b", "u")
    await store.get("c", "u")  # triggers eviction of the LRU ("a")

    assert a is not None and a.id in rest.deleted
    names = {r.name for r in await schemas.AppEmojiCache.all_for_app(APP)}
    assert names == {"b", "c"}
    assert len(store._emoji) == 2


async def test_store_start_adopts_live_and_drops_dead_rows() -> None:
    store, rest = _store()
    # A live emoji Discord already has, not tracked in the DB.
    rest._emojis[7] = _FakeEmoji(7, "preexisting")  # noqa: SLF001
    # A stale DB row whose emoji no longer exists.
    await schemas.AppEmojiCache.upsert(APP, "ghost", 5, "u")

    await store.start()

    names = {r.name for r in await schemas.AppEmojiCache.all_for_app(APP)}
    assert "preexisting" in names  # adopted
    assert "ghost" not in names  # dropped


# --- get_by_emoji_id ---------------------------------------------------------------

ANCHOR_APP = 9999  # a different bot's application store, sharing the same table


async def test_get_by_emoji_id_crosses_apps() -> None:
    await schemas.AppEmojiCache.upsert(APP, "mine", 111, "u")
    await schemas.AppEmojiCache.upsert(ANCHOR_APP, "theirs", 222, "http://i/t.png")
    row = await schemas.AppEmojiCache.get_by_emoji_id(222)
    assert row is not None
    assert row.app_id == ANCHOR_APP and row.name == "theirs"
    assert await schemas.AppEmojiCache.get_by_emoji_id(999999) is None


# --- rewrite_item_emoji ------------------------------------------------------------


async def _beacon_store() -> tuple[AppEmojiStore, _FakeRest]:
    store, rest = _store()
    await store.start()  # sets store.app_id == APP
    return store, rest


async def test_rewrite_foreign_item_emoji_is_rewritten() -> None:
    store, rest = await _beacon_store()
    await schemas.AppEmojiCache.upsert(ANCHOR_APP, "chroma_rush", 555, "http://i/c.png")
    out = await rewrite_item_emoji(store, "loot <:chroma_rush:555> drop")
    assert rest.created == ["chroma_rush"]  # uploaded to beacon's own store
    new_id = max(rest._emojis)  # the freshly-uploaded beacon emoji id (!= 555)
    assert out == f"loot <:chroma_rush:{new_id}> drop"


async def test_rewrite_own_emoji_left_untouched() -> None:
    store, rest = await _beacon_store()
    await schemas.AppEmojiCache.upsert(APP, "mine", 777, "http://i/m.png")
    text = "x <:mine:777> y"
    assert await rewrite_item_emoji(store, text) == text
    assert rest.created == []  # already ours → no upload


async def test_rewrite_unknown_emoji_left() -> None:
    store, _ = await _beacon_store()
    text = "guild <:some_guild_emoji:424242> here"  # no cache row for this id
    assert await rewrite_item_emoji(store, text) == text


async def test_rewrite_bare_token_left() -> None:
    store, _ = await _beacon_store()
    text = "a :hand_cannon: token"  # no id → not a rendered mention
    assert await rewrite_item_emoji(store, text) == text


async def test_rewrite_animated_form() -> None:
    store, rest = await _beacon_store()
    await schemas.AppEmojiCache.upsert(ANCHOR_APP, "spin", 888, "http://i/s.png")
    out = await rewrite_item_emoji(store, "<a:spin:888>")
    new_id = max(rest._emojis)
    assert out == f"<:spin:{new_id}>"  # replaced with beacon's own mention


async def test_rewrite_upload_failure_leaves_mention() -> None:
    store, _ = await _beacon_store()
    # Foreign item emoji whose icon can't be uploaded (empty url → fake raises).
    await schemas.AppEmojiCache.upsert(ANCHOR_APP, "broken", 999, "")
    text = "keep <:broken:999> verbatim"
    assert await rewrite_item_emoji(store, text) == text


async def test_rewrite_item_emoji_in_message_all_surfaces() -> None:
    store, rest = await _beacon_store()
    await schemas.AppEmojiCache.upsert(ANCHOR_APP, "gjallarhorn", 555, "http://i/g.png")

    container = h.impl.ContainerComponentBuilder()
    container.add_text_display("cv2 <:gjallarhorn:555>")
    hmsg = HMessage(
        content="content <:gjallarhorn:555>",
        embeds=[h.Embed(description="embed <:gjallarhorn:555>")],
        components=[container],
    )

    await rewrite_item_emoji_in_message(store, hmsg)

    new_id = max(rest._emojis)  # beacon's freshly-uploaded id
    mention = f"<:gjallarhorn:{new_id}>"
    assert mention in hmsg.content
    desc = hmsg.embeds[0].description
    assert desc is not None and mention in desc
    text_display = t.cast(t.Any, hmsg.components[0]).components[0]
    assert mention in text_display.content
    assert rest.created == ["gjallarhorn"]  # one upload despite three surfaces
