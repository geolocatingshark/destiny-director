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

"""HMessage: a mutable, mergeable representation of a Discord message.

Wraps message content, embeds and attachments with helpers to merge several
source messages into a single message payload (used when mirroring and
announcing).
"""

from __future__ import annotations

import logging
import typing as t

import attr
import hikari as h

from .constants import DEFAULT_COLOR
from .embed import MultiImageEmbedList


class HMessageEmbed(h.Embed):
    def __eq__(self, other: t.Any) -> bool:
        if not isinstance(other, type(self)):
            if isinstance(other, h.Embed):
                other = type(self).from_embed(other)
            else:
                return False

        for attrsib in self.__slots__:
            # Image equality override
            if attrsib == "_image":
                if str(getattr(self, attrsib).url) != str(getattr(other, attrsib).url):
                    break
            elif getattr(self, attrsib) != getattr(other, attrsib):
                break
        else:
            return True
        return False

    @classmethod
    def from_embed(cls, embed: h.Embed):
        return cls.from_received_embed(
            title=embed._title,
            description=embed._description,
            url=embed._url,
            color=embed._color,
            timestamp=embed._timestamp,
            image=embed._image,
            thumbnail=embed._thumbnail,
            video=embed._video,
            author=embed._author,
            provider=embed._provider,
            footer=embed._footer,
            fields=embed._fields,
        )


@attr.s
class HMessage:
    """A prototype for a message to be sent to a channel."""

    content: str = attr.ib(default="", converter=str)
    embeds: list[h.Embed] = attr.ib(default=attr.Factory(list))
    embed_default_colour: h.Color = attr.ib(
        default=DEFAULT_COLOR, converter=h.Color, eq=False
    )
    attachments: list[h.Attachment] = attr.ib(default=attr.Factory(list))
    # Components V2 builders. When non-empty the message is sent as a CV2 message
    # (``IS_COMPONENTS_V2`` flag, no content/embeds — they are mutually exclusive in
    # Discord). Used by the eververse autopost.
    components: list[h.api.ComponentBuilder] = attr.ib(
        default=attr.Factory(list), eq=False
    )
    id: int | None = attr.ib(default=0, converter=int, eq=False)

    @content.validator
    def _validate_content(self, attribute, value):
        if len(value) > 2000:
            raise ValueError(
                "Cannot send more than 2000 characters in a single message"
            )

    @embeds.validator
    def _validate_embeds(self, attribute, value):
        if len(value) > 10:
            raise ValueError("Cannot send more than 10 embeds in a single message")

    @attachments.validator
    def _validate_attachments(self, attribute, value):
        if len(value) > 10:
            raise ValueError("Cannot send more than 10 attachments in a single message")

    @classmethod
    def from_message(cls, message: h.PartialMessage) -> HMessage:
        """Create a HMessage instance from a message.

        A Components V2 source message (``IS_COMPONENTS_V2``) carries no content or
        embeds; its components are rebuilt into sendable builders so callers (e.g. the
        navigator) can re-render it. An unsupported CV2 component type is captured as no
        components — the caller degrades to "no data" rather than crashing.
        """
        components: list[h.api.ComponentBuilder] = []
        flags = message.flags if isinstance(message.flags, h.MessageFlag) else None
        raw_components = (
            message.components
            if not isinstance(message.components, h.UndefinedType)
            else []
        )
        if (
            flags is not None
            and h.MessageFlag.IS_COMPONENTS_V2 in flags
            and raw_components
        ):
            # Local import: avoids any hmessage<->common module-load cycle.
            from ..common.components import rebuild_components

            try:
                components = rebuild_components(raw_components)
            except NotImplementedError:
                logging.warning(
                    "HMessage.from_message: message %s has an unrebuildable CV2 "
                    "component; captured with no components.",
                    message.id,
                )

        return cls(
            content=message.content or "",
            embeds=[
                HMessageEmbed.from_embed(embed)
                for embed in (
                    message.embeds
                    if not isinstance(message.embeds, h.UndefinedType)
                    else []
                )
            ],
            attachments=[
                att.url
                for att in (
                    message.attachments
                    if not isinstance(message.attachments, h.UndefinedType)
                    else []
                )
            ],
            components=components,
            id=message.id,
        )

    def to_message_kwargs(self) -> dict[str, t.Any]:
        """Convert the HMessage instance into a dict of kwargs to be passed to
        `hikari.Messageable.send`."""
        if self.components:
            # A Components V2 message uses components exclusively — no content/embeds.
            return {
                "components": self.components,
                "flags": h.MessageFlag.IS_COMPONENTS_V2,
            }
        return {
            "content": self.content,
            "embeds": self.embeds,
            "attachments": self.attachments,
        }

    def __add__(self, other):
        if not isinstance(other, self.__class__):
            raise TypeError(f"Cannot add HMessage to {other.__class__.__name__}")

        if self.content.endswith("\n") or other.content.startswith("\n"):
            use_endline = False
        else:
            use_endline = True

        return self.__class__(
            content=(self.content + ("\n" if use_endline else "") + other.content),
            embeds=self.embeds + other.embeds,
            attachments=self.attachments + other.attachments,
            # Preserve CV2 components across a merge so accumulate() over a period's
            # messages keeps them. to_message_kwargs prefers components when present.
            components=self.components + other.components,
        )

    def merge_content_into_embed(
        self, embed_no: int = 0, prepend: bool = True
    ) -> HMessage:
        """Merge the content of a message into the description of an embed.

        Args:
            embed_no (int, optional): The index of the embed to merge the content into.
            prepend (bool, optional): Whether to prepend the content to the embed
                description. If False, the content will be appended to the embed
                description. Defaults to True.
        """
        content = str(self.content or "")
        self.content = ""

        if not self.embeds:
            self.embeds = [h.Embed(description=content, color=DEFAULT_COLOR)]
            return self

        embed_no = int(embed_no) % (len(self.embeds) or 1)

        if not isinstance(self.embeds[embed_no].description, str):
            self.embeds[embed_no].description = ""

        if prepend:
            self.embeds[embed_no].description = (
                content + "\n\n" + (self.embeds[embed_no].description or "")
            )
        else:
            self.embeds[embed_no].description = (
                (self.embeds[embed_no].description or "") + "\n\n" + content
            )

        return self

    def merge_url_as_image_into_embed(
        self,
        url: str | None,
        embed_no: int = 0,
        designator: int = 0,
        default_url: str | None = None,
    ):
        if url is None:
            logging.warning("Cannot merge NoneType URL into embed")
            return

        if not self.embeds:
            self.embeds = [h.Embed(color=DEFAULT_COLOR)]

        embed_no = int(embed_no) % len(self.embeds)

        embed = self.embeds.pop(embed_no)
        # Fall back to ``default_url`` for the embed's canonical url when the
        # embed carries none of its own — otherwise from_embed raises (an embed
        # synthesised with no url cannot anchor a valid multi-image group).
        embeds = MultiImageEmbedList.from_embed(
            embed,
            designator,
            [url],
            default_url=default_url or "",
        )

        for embed in embeds[::-1]:
            self.embeds.insert(embed_no, embed)

        return self

    def remove_all_embed_thumbnails(self):
        for embed in self.embeds:
            embed.set_thumbnail(None)
        return self

    def merge_attachements_into_embed(
        self,
        embed_no: int = -1,
        designator: int = 0,
        new_embed: bool = False,
        default_url: str | None = None,
    ) -> HMessage:
        """Merge the attachments of a message into the embed.

        Args:
            embed_no (int, optional): The index of the embed to merge the
                attachments into.
            designator (int, optional): The designator to use for the embed.
                Defaults to 0.
            new_embed (bool, optional): Whether to create a new embed for the
                attachments. Sets embed_no to the last embed. Defaults to False.
        """
        if not self.embeds:
            self.embeds = [h.Embed(color=DEFAULT_COLOR)]

        if new_embed:
            embed_no = len(self.embeds)
            self.embeds.append(h.Embed(color=DEFAULT_COLOR, description="."))

        embed_no = int(embed_no) % len(self.embeds)

        attachments_to_embeds_list = []
        attachments_remaining_list = []
        for attachment in self.attachments:
            if hasattr(attachment, "media_type") and str(
                attachment.media_type
            ).startswith("image"):
                attachments_to_embeds_list.append(attachment.url)
            else:
                attachments_remaining_list.append(attachment)

        embeds = MultiImageEmbedList.from_embed(
            self.embeds.pop(embed_no),
            designator,
            attachments_to_embeds_list,
            default_url=default_url or "",
        )

        for embed in embeds[::-1]:
            self.embeds.insert(embed_no, embed)

        self.attachments = attachments_remaining_list

        return self
