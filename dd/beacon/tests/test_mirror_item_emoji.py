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

"""Mirror payload-shaping tests: the item-emoji rewrite itself is covered by
HMessage.map_text (dd/hmessage/tests) and rewrite_item_emoji_in_message
(dd/anchor/tests/test_emoji_store.py); here we pin that ``_send_payload`` /
``_cv2_components_for`` add the per-destination role ping WITHOUT mutating the shared,
once-rewritten source HMessage cached in ``_source_for``."""

import typing as t

import hikari as h

from dd.beacon import mirror_worker as mw
from dd.hmessage import HMessage

ROLE = {123: 999}  # dest channel -> role to ping


def _cv2_hmsg() -> HMessage:
    container = h.impl.ContainerComponentBuilder(
        accent_color=h.Color(0xABCDEF), spoiler=True
    )
    container.add_text_display("body")
    comps: list[h.api.ComponentBuilder] = [container]
    return HMessage(components=comps)


def _ping_texts(container: t.Any) -> list[str]:
    return [
        c.content
        for c in container.components
        if isinstance(c, h.impl.TextDisplayComponentBuilder)
    ]


def test_cv2_ping_cloned_without_mutating_source() -> None:
    hmsg = _cv2_hmsg()
    src = t.cast(t.Any, hmsg.components[0])
    src_children = len(src.components)

    out = mw._cv2_components_for(hmsg, 123, ROLE)

    out_container = t.cast(t.Any, out[0])
    assert any("<@&999>" in text for text in _ping_texts(out_container))  # ping added
    assert out_container is not src  # a clone, not the shared source
    assert out_container.accent_color == h.Color(0xABCDEF)  # clone keeps accent
    assert out_container.is_spoiler is True  # ...and spoiler
    # the shared source container is untouched
    assert len(src.components) == src_children
    assert not any("<@&999>" in text for text in _ping_texts(src))


def test_cv2_no_ping_shares_source_component() -> None:
    hmsg = _cv2_hmsg()
    out = mw._cv2_components_for(hmsg, 123, {})  # no role for this dest
    assert out[0] is hmsg.components[0]  # no clone, shared verbatim


def test_send_payload_cv2_branch() -> None:
    payload = mw._send_payload(_cv2_hmsg(), 123, {})
    assert "components" in payload
    assert payload["flags"] == h.MessageFlag.IS_COMPONENTS_V2
    assert "content" not in payload


def test_send_payload_plain_branch_appends_ping() -> None:
    hmsg = HMessage(content="hi", embeds=[h.Embed(description="d")])
    payload = mw._send_payload(hmsg, 123, {123: 5})
    assert "components" not in payload
    assert "<@&5>" in payload["content"]
    assert payload["embeds"] == hmsg.embeds


# --- golden-master characterization of _send_payload ---------------------------
# These pin the EXACT payload shape of the current _send_payload so the planned
# refactor onto HMessage.with_appended_text + to_message_kwargs stays byte-identical.
# Highest-value guard: role_mentions=True is what makes a mirrored ping actually fire.


def _no_container_hmsg() -> HMessage:
    """A CV2 HMessage whose only component is a top-level text display (no
    container)."""
    comps: list[h.api.ComponentBuilder] = [
        h.impl.TextDisplayComponentBuilder(content="body")
    ]
    return HMessage(components=comps)


def test_send_payload_role_mentions_true_in_every_branch() -> None:
    # plain, plain+ping, cv2, cv2+ping — all must set role_mentions=True so the
    # inline spoilered role mention is actually allowed to ping.
    assert mw._send_payload(HMessage(content="hi"), 123, {})["role_mentions"] is True
    assert mw._send_payload(HMessage(content="hi"), 123, ROLE)["role_mentions"] is True
    assert mw._send_payload(_cv2_hmsg(), 123, {})["role_mentions"] is True
    assert mw._send_payload(_cv2_hmsg(), 123, ROLE)["role_mentions"] is True


def test_send_payload_plain_no_ping_full_dict() -> None:
    embed = h.Embed(description="d")
    hmsg = HMessage(content="hi", embeds=[embed], attachments=["u"])
    payload = mw._send_payload(hmsg, 123, {})  # no role for this dest
    assert payload == {
        "content": "hi",
        "attachments": ["u"],
        "embeds": [embed],
        "role_mentions": True,
    }
    assert "flags" not in payload
    assert "components" not in payload


def test_send_payload_plain_ping_exact_content_and_passthrough() -> None:
    embed = h.Embed(description="d")
    hmsg = HMessage(content="hi", embeds=[embed], attachments=["u"])
    payload = mw._send_payload(hmsg, 123, {123: 5})
    assert payload["content"] == "hi\n\n||<@&5>||"  # blank line before the ping
    assert payload["attachments"] == ["u"]  # attachments pass through
    assert payload["embeds"] == [embed]  # embeds pass through


def test_send_payload_plain_ping_empty_content_is_bare_ping() -> None:
    payload = mw._send_payload(HMessage(content=""), 123, {123: 5})
    assert payload["content"] == "||<@&5>||"  # no leading newlines


def test_send_payload_plain_ping_strips_trailing_newlines() -> None:
    payload = mw._send_payload(HMessage(content="hi\n\n\n"), 123, {123: 5})
    assert payload["content"] == "hi\n\n||<@&5>||"


def test_send_payload_cv2_ping_lands_in_first_container() -> None:
    payload = mw._send_payload(_cv2_hmsg(), 123, ROLE)
    assert payload["flags"] == h.MessageFlag.IS_COMPONENTS_V2
    assert payload["role_mentions"] is True
    assert "content" not in payload
    first_container = t.cast(t.Any, payload["components"][0])
    assert any("<@&999>" in text for text in _ping_texts(first_container))


def test_send_payload_cv2_ping_no_container_appends_top_level() -> None:
    hmsg = _no_container_hmsg()
    payload = mw._send_payload(hmsg, 123, ROLE)
    comps = t.cast(list[t.Any], payload["components"])
    assert len(comps) == 2  # original text display + appended ping
    assert isinstance(comps[-1], h.impl.TextDisplayComponentBuilder)
    assert comps[-1].content == "||<@&999>||"


def test_send_payload_cv2_no_ping_shares_component_and_omits_content() -> None:
    hmsg = _cv2_hmsg()
    payload = mw._send_payload(hmsg, 123, {})
    assert payload["components"][0] is hmsg.components[0]  # shared verbatim
    assert payload["flags"] == h.MessageFlag.IS_COMPONENTS_V2
    assert "content" not in payload
