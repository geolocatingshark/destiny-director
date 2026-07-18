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

# Pure data-transformation tests for HMessage — no Discord I/O.

import typing as t

import hikari as h
import pytest

from dd.hmessage.message import HMessage, HMessageEmbed


def _embed_with_image(title: str = "t", description: str = "d") -> h.Embed:
    # HMessageEmbed.__eq__ dereferences ``_image.url`` unconditionally, so only
    # compare embeds that actually carry an image.
    embed = h.Embed(title=title, description=description)
    embed.set_image("https://example.com/i.png")
    return embed


# --- validators ----------------------------------------------------------------


def test_content_over_2000_chars_raises():
    with pytest.raises(ValueError):
        HMessage(content="x" * 2001)


def test_more_than_ten_embeds_raises():
    with pytest.raises(ValueError):
        HMessage(embeds=[h.Embed() for _ in range(11)])


def test_more_than_ten_attachments_raises():
    with pytest.raises(ValueError):
        HMessage(attachments=["a"] * 11)


# --- to_message_kwargs ---------------------------------------------------------


def test_to_message_kwargs_round_trips_fields():
    embed = h.Embed(description="body")
    msg = HMessage(content="hi", embeds=[embed])
    kwargs = msg.to_message_kwargs()
    assert kwargs == {"content": "hi", "embeds": [embed], "attachments": []}


def test_to_message_kwargs_role_mentions_passthrough():
    # Default omits the key entirely, so existing callers are unaffected.
    assert "role_mentions" not in HMessage(content="hi").to_message_kwargs()
    # Explicit value flows through in the plain branch...
    plain = HMessage(content="hi").to_message_kwargs(role_mentions=True)
    assert plain["role_mentions"] is True
    # ...and in the CV2 branch.
    cv2 = HMessage(components=[h.impl.ContainerComponentBuilder()])
    kwargs = cv2.to_message_kwargs(role_mentions=True)
    assert kwargs["flags"] == h.MessageFlag.IS_COMPONENTS_V2
    assert kwargs["role_mentions"] is True


# --- with_appended_text --------------------------------------------------------


def _cv2_container_hmsg() -> HMessage:
    container = h.impl.ContainerComponentBuilder(
        accent_color=h.Color(0xABCDEF), spoiler=True
    )
    container.add_text_display("body")
    comps: list[h.api.ComponentBuilder] = [container]
    return HMessage(components=comps)


def _text_display_contents(container: t.Any) -> list[str]:
    return [
        c.content
        for c in container.components
        if isinstance(c, h.impl.TextDisplayComponentBuilder)
    ]


def test_with_appended_text_plain_adds_blank_line():
    assert HMessage(content="hi").with_appended_text("ping").content == "hi\n\nping"


def test_with_appended_text_plain_empty_content_is_bare_text():
    assert HMessage(content="").with_appended_text("ping").content == "ping"


def test_with_appended_text_plain_strips_trailing_newlines():
    out = HMessage(content="hi\n\n\n").with_appended_text("ping")
    assert out.content == "hi\n\nping"


def test_with_appended_text_does_not_mutate_source():
    src = HMessage(content="hi")
    src.with_appended_text("ping")
    assert src.content == "hi"  # original untouched


def test_with_appended_text_cv2_clones_first_container_without_mutation():
    hmsg = _cv2_container_hmsg()
    src = t.cast(t.Any, hmsg.components[0])
    src_children = len(src.components)

    out_container = t.cast(t.Any, hmsg.with_appended_text("ping").components[0])

    assert out_container is not src  # a clone, not the shared source
    assert "ping" in _text_display_contents(out_container)  # ping added to the clone
    assert out_container.accent_color == h.Color(0xABCDEF)  # accent preserved
    assert out_container.is_spoiler is True  # ...and spoiler
    # the shared source container is untouched
    assert len(src.components) == src_children
    assert "ping" not in _text_display_contents(src)


def test_with_appended_text_cv2_no_container_appends_top_level():
    comps: list[h.api.ComponentBuilder] = [
        h.impl.TextDisplayComponentBuilder(content="body")
    ]
    out = HMessage(components=comps).with_appended_text("ping")
    assert len(out.components) == 2  # original text display + appended ping
    assert isinstance(out.components[-1], h.impl.TextDisplayComponentBuilder)
    assert out.components[-1].content == "ping"


# --- fit_content ---------------------------------------------------------------


def test_fit_content_truncates_over_budget_and_reports_original_length():
    msg = HMessage(content="x" * 50)
    length = msg.fit_content(10)
    assert length == 50  # pre-trim length reported
    assert msg.content == "x" * 10  # truncated in place


def test_fit_content_is_noop_within_budget():
    msg = HMessage(content="short")
    assert msg.fit_content(10) == 5
    assert msg.content == "short"  # untouched


# --- __add__ -------------------------------------------------------------------


def test_add_joins_content_with_newline():
    assert (HMessage(content="a") + HMessage(content="b")).content == "a\nb"


def test_add_does_not_double_newline_when_already_present():
    assert (HMessage(content="a\n") + HMessage(content="b")).content == "a\nb"


def test_add_combines_embeds_and_attachments():
    merged = HMessage(embeds=[h.Embed()], attachments=["x"]) + HMessage(
        embeds=[h.Embed()], attachments=["y"]
    )
    assert len(merged.embeds) == 2
    assert merged.attachments == ["x", "y"]


def test_add_rejects_non_hmessage():
    with pytest.raises(TypeError):
        _ = HMessage() + object()


# --- merge_content_into_embed --------------------------------------------------


def test_merge_content_creates_embed_when_none():
    msg = HMessage(content="hello")
    msg.merge_content_into_embed()
    assert msg.content == ""
    assert len(msg.embeds) == 1
    assert msg.embeds[0].description == "hello"


def test_merge_content_prepends_into_existing_embed():
    msg = HMessage(content="hi", embeds=[h.Embed(description="body")])
    msg.merge_content_into_embed(prepend=True)
    assert msg.content == ""
    assert msg.embeds[0].description == "hi\n\nbody"


# --- merge_url_as_image_into_embed (default_url safety net) ---------------------


def test_merge_url_falls_back_to_default_url_when_embed_has_none():
    # Regression: a synthesised embed has no url. Without the default_url safety
    # net this produced an invalid relative url; now it anchors on default_url.
    msg = HMessage(content="")
    msg.merge_url_as_image_into_embed(
        "https://example.com/i.png", 0, default_url="https://example.com/canonical"
    )
    assert str(msg.embeds[0].url).startswith("https://example.com/canonical")


def test_merge_url_raises_when_no_url_available():
    # With neither an embed url nor a default_url, the (now live) guard fires
    # instead of silently emitting an invalid embed url.
    msg = HMessage(content="")
    with pytest.raises(ValueError):
        msg.merge_url_as_image_into_embed("https://example.com/i.png", 0)


# --- remove_all_embed_thumbnails -----------------------------------------------


def test_remove_all_embed_thumbnails():
    embed = h.Embed()
    embed.set_thumbnail("https://example.com/t.png")
    msg = HMessage(embeds=[embed])
    msg.remove_all_embed_thumbnails()
    assert msg.embeds[0].thumbnail is None


# --- HMessageEmbed equality ----------------------------------------------------


def test_hmessage_embed_equals_matching_hikari_embed():
    a = HMessageEmbed.from_embed(_embed_with_image("t", "d"))
    assert a == _embed_with_image("t", "d")


def test_hmessage_embed_differs_on_title():
    a = HMessageEmbed.from_embed(_embed_with_image("t", "d"))
    assert a != _embed_with_image("other", "d")


def test_hmessage_embed_not_equal_to_non_embed():
    a = HMessageEmbed.from_embed(_embed_with_image())
    assert a != "not an embed"


# --- Components V2 capture / merge -----------------------------------------------


class _FakeMessage:
    """Minimal PartialMessage stand-in for HMessage.from_message."""

    def __init__(self, *, flags, components, embeds=None, content="", id=7):
        self.flags = flags
        self.components = components
        self.embeds = embeds if embeds is not None else []
        self.attachments = []
        self.content = content
        self.id = id


def test_from_message_captures_cv2_components(monkeypatch):
    rebuilt = [object()]
    seen = {}

    def _fake_rebuild(components):
        seen["arg"] = components
        return rebuilt

    monkeypatch.setattr("dd.common.components.rebuild_components", _fake_rebuild)

    models = ["container-model"]
    msg = _FakeMessage(flags=h.MessageFlag.IS_COMPONENTS_V2, components=models)
    hmsg = HMessage.from_message(msg)

    assert hmsg.components == rebuilt
    assert seen["arg"] == models
    assert hmsg.embeds == []


def test_from_message_embed_message_has_no_components():
    msg = _FakeMessage(
        flags=h.MessageFlag.NONE, components=[], embeds=[_embed_with_image()]
    )
    hmsg = HMessage.from_message(msg)

    assert hmsg.components == []
    assert len(hmsg.embeds) == 1


def test_from_message_unrebuildable_cv2_degrades_to_no_components(monkeypatch):
    def _boom(components):
        raise NotImplementedError("media gallery")

    monkeypatch.setattr("dd.common.components.rebuild_components", _boom)

    msg = _FakeMessage(flags=h.MessageFlag.IS_COMPONENTS_V2, components=["x"])
    hmsg = HMessage.from_message(msg)

    assert hmsg.components == []


def test_add_concatenates_cv2_components():
    a = HMessage(components=["a"])
    b = HMessage(components=["b"])
    assert (a + b).components == ["a", "b"]


# --- map_text / map_text_async -------------------------------------------------

_IN, _OUT = "<:x:1>", "<:x:2>"


def _sub(text: str) -> str:
    return text.replace(_IN, _OUT)


async def _asub(text: str) -> str:
    return text.replace(_IN, _OUT)


def _cv2_message() -> HMessage:
    container = h.impl.ContainerComponentBuilder()
    container.add_component(
        h.impl.TextDisplayComponentBuilder(content=f"top {_IN}", id=7)
    )
    section = h.impl.SectionComponentBuilder(
        accessory=h.impl.ThumbnailComponentBuilder(media="http://i/y.png")
    )
    section.add_text_display(f"sec {_IN}")
    container.add_component(section)
    container.add_separator()  # non-text child: must survive untouched
    return HMessage(components=[container])


def _all_cv2_text(hmsg: HMessage) -> list[str]:
    out: list[str] = []

    def walk(comps):
        for c in comps:
            if isinstance(c, h.impl.TextDisplayComponentBuilder):
                out.append(c.content)
            elif isinstance(
                c, h.impl.ContainerComponentBuilder | h.impl.SectionComponentBuilder
            ):
                walk(c.components)

    walk(hmsg.components)
    return out


def test_map_text_walks_content_and_all_embed_surfaces():
    embed = h.Embed(title=f"T {_IN}", description=f"D {_IN}")
    embed.add_field(name=f"FN {_IN}", value=f"FV {_IN}")
    embed.set_author(name=f"A {_IN}", icon="http://i/a.png")
    embed.set_footer(text=f"F {_IN}", icon="http://i/f.png")
    hmsg = HMessage(content=f"C {_IN}", embeds=[embed])

    assert hmsg.map_text(_sub) is hmsg  # returns self
    assert hmsg.content == f"C {_OUT}"
    e = hmsg.embeds[0]
    assert e.description == f"D {_OUT}"
    assert e.title == f"T {_OUT}"
    assert e.fields[0].name == f"FN {_OUT}"
    assert e.fields[0].value == f"FV {_OUT}"
    assert e.author is not None and e.author.name == f"A {_OUT}"
    assert e.footer is not None and e.footer.text == f"F {_OUT}"
    # author/footer icons preserved through the re-set
    assert e.author.icon is not None and e.author.icon.url == "http://i/a.png"
    assert e.footer.icon is not None and e.footer.icon.url == "http://i/f.png"


def test_map_text_walks_cv2_preserving_id_and_non_text():
    hmsg = _cv2_message()
    hmsg.map_text(_sub)
    texts = _all_cv2_text(hmsg)
    assert f"top {_OUT}" in texts
    assert f"sec {_OUT}" in texts
    assert all(_IN not in t for t in texts)
    # the replaced top text display kept its id, and the separator survived
    children = t.cast(t.Any, hmsg.components[0]).components
    top = next(c for c in children if getattr(c, "content", None) == f"top {_OUT}")
    assert top.id == 7
    assert any(isinstance(c, h.impl.SeparatorComponentBuilder) for c in children)


def test_map_text_leaves_unmatched_surfaces_untouched():
    hmsg = HMessage(content="no tokens here")
    hmsg.map_text(_sub)
    assert hmsg.content == "no tokens here"


@pytest.mark.asyncio
async def test_map_text_async_matches_sync():
    hmsg = _cv2_message()
    assert await hmsg.map_text_async(_asub) is hmsg
    assert all(_IN not in t for t in _all_cv2_text(hmsg))


def test_fit_cv2_text_truncates_over_budget_and_returns_pre_length() -> None:
    from dd.common.components import cv2_text_length

    container = h.impl.ContainerComponentBuilder()
    container.add_text_display("y" * 8000)
    hmsg = HMessage(components=[container])

    length = hmsg.fit_cv2_text(budget=100)
    assert length == 8000  # pre-trim length returned so a caller can alert
    assert cv2_text_length(hmsg.components) <= 100  # trimmed in place


def test_fit_cv2_text_noop_within_budget() -> None:
    container = h.impl.ContainerComponentBuilder()
    container.add_text_display("small")
    hmsg = HMessage(components=[container])
    before = hmsg.components

    assert hmsg.fit_cv2_text(budget=1000) == 5
    assert hmsg.components == before  # untouched


def test_hikari_component_internals_canary():
    """Pin the hikari builder internals the CV2 walk depends on.

    If this fails after a hikari bump, revisit the ``_component_text_surfaces`` /
    ``_child_components`` walk in message.py: it reaches the *private* ``_components``
    backing list because the public ``.components`` getter returns a copy, and replaces
    a matched text display with a fresh builder preserving its ``id``. The mirror's
    per-dest container clone also reads ``accent_color`` / ``is_spoiler``.
    """
    container = h.impl.ContainerComponentBuilder(
        accent_color=h.Color(0x123456), spoiler=True
    )
    container.add_text_display("hi")
    # `.components` is a *copy*; `_components` is the live backing list.
    assert container.components is not container.components
    live = getattr(container, "_components", None)
    assert isinstance(live, list)
    live.append(h.impl.TextDisplayComponentBuilder(content="added"))
    assert len(container.components) == 2
    # public getters the container clone relies on
    assert container.accent_color == h.Color(0x123456)
    assert container.is_spoiler is True
    # a replaced text display round-trips content + id
    td = h.impl.TextDisplayComponentBuilder(content="x", id=7)
    assert td.content == "x" and td.id == 7
