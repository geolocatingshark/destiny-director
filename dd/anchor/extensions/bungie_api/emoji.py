"""Bridge from a :class:`DestinyItem` to an inline emoji string.

The pure-lazy :class:`~dd.common.emoji_store.AppEmojiStore` speaks only
``(name, icon_url)`` and knows nothing about Destiny. This thin adapter supplies the
Bungie-specific knowledge — the item's normalised emoji name and its Bungie.net icon URL
— and falls back to the legacy per-type ``:type:`` token when an item has no icon or the
upload fails, so output degrades gracefully instead of showing a broken ``:name:``.
"""

from __future__ import annotations

from dd.common.emoji_store import AppEmojiStore, emoji_name

from .models import DestinyItem


async def item_emoji(
    store: AppEmojiStore, item: DestinyItem, *, fallback_to_type: bool = True
) -> str:
    """Return the inline emoji string to use for ``item``.

    On the happy path this uploads the icon once and returns a rendered ``<:name:id>``
    mention from the posting bot's own application-emoji store. On failure it returns
    the legacy ``:{item.expected_emoji_name}:`` token (resolved later by the guild-emoji
    substituter) when ``fallback_to_type`` is set, otherwise an empty string.
    """
    icon_url = item.icon_url
    if icon_url:
        emoji = await store.get(emoji_name(item.name), icon_url)
        if emoji is not None:
            return str(emoji)
    return f":{item.expected_emoji_name}:" if fallback_to_type else ""
