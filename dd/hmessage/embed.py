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


from __future__ import annotations

import contextlib
import typing as t

import hikari as h
import yarl

from .constants import DEFAULT_COLOR, DESIGNATOR_PARAMETER_NAME


class MultiImageEmbedList(list[h.Embed]):
    """A list of embeds with the same URL property and different image properties."""

    def __init__(
        self,
        url: str,
        designator: int = 0,
        images: list[str] | None = None,
        **kwargs: t.Any,
    ):
        super().__init__()

        if images is None:
            images = []

        if kwargs.get("image"):
            raise ValueError(
                "Cannot set image property when using MultiImageEmbedList, "
                + "use images instead."
            )

        if not kwargs.get("description"):
            kwargs["description"] = ""

        if not kwargs.get("color") or kwargs.get("colour"):
            kwargs["color"] = DEFAULT_COLOR

        yarl_url: yarl.URL = yarl.URL(str(url))
        # Get the DESIGNATOR_PARAMETER from the url query
        # if it doesn't exist then use the designator parameter from the function
        # args.
        # if that doesn't exist then use the default value of 0
        designator_str: str = yarl_url.query.get(
            DESIGNATOR_PARAMETER_NAME, str(designator or 0)
        )
        embed = h.Embed(
            url=str(yarl_url % {DESIGNATOR_PARAMETER_NAME: designator_str}),
            **kwargs,
        )

        with contextlib.suppress(IndexError):
            embed.set_image(images.pop(0))

        self.append(embed)

        for image in images:
            self.add_image(image)

    def add_image(self, image: str) -> MultiImageEmbedList:
        """Add an image to the MultiImageEmbedList instance."""
        if self[-1].image:
            embed = h.Embed(
                url=self[0].url,
                description="",
                color=DEFAULT_COLOR,
            )
            embed.set_image(image)
            self.append(embed)
        else:
            self[-1].set_image(image)
        return self

    def add_images(self, images: list[str]) -> MultiImageEmbedList:
        """Add multiple images to the MultiImageEmbedList instance."""
        for image in images:
            self.add_image(image)
        return self

    @classmethod
    def from_embed(
        cls,
        embed: h.Embed,
        designator: int = 0,
        images: list[str] | None = None,
        default_url: str | yarl.URL = "",
    ) -> MultiImageEmbedList:
        if images is None:
            images = []

        # Resolve (and validate) the canonical url *before* constructing: the
        # constructor always appends the designator query param, so the embed's
        # ``.url`` is never empty afterwards — checking it post-construction would
        # never fire. Multi-image grouping needs a real shared url, so require one.
        resolved_url = embed.url or str(default_url)
        if not resolved_url:
            raise ValueError(
                "If no default_url is provided then embeds must have a url."
            )

        # Create a MultiImageEmbed instance
        multi_image_embed: list[h.Embed] = cls(
            url=resolved_url,
            designator=designator,
            description=embed.description,
            title=embed.title,
            color=embed.color or DEFAULT_COLOR,
            timestamp=embed.timestamp,
        )

        if embed.image:
            multi_image_embed[0].set_image(embed.image.url)
        if embed.footer:
            multi_image_embed[0].set_footer(embed.footer.text, icon=embed.footer.icon)
        if embed.thumbnail:
            multi_image_embed[0].set_thumbnail(embed.thumbnail.url)
        if embed.author:
            multi_image_embed[0].set_author(
                name=embed.author.name, url=embed.author.url, icon=embed.author.icon
            )

        for field in embed.fields:
            multi_image_embed[0].add_field(
                field.name, field.value, inline=field.is_inline
            )

        # Loop through the image URLs and create and append new embeds with
        # different image properties
        multi_image_embed.add_images(images)
        # Return the MultiImageEmbed instance
        return multi_image_embed
