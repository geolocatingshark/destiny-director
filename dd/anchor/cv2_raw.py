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

"""Raw Components V2 send/load helpers, independent of any per-component mapper.

The interactive Components V2 builder keeps its state as raw component-payload dicts
(Discord's own JSON shape). :class:`RawComponentBuilder` sends such a dict verbatim as
a bot message, and :func:`fetch_raw_message_components` reads an existing post's
components straight back as the same dicts — so the builder round-trips every type with
full fidelity and no typed model layer.
"""

import typing as t

import aiohttp
import hikari as h

from ..common import cfg

_DISCORD_API_BASE = "https://discord.com/api/v10"


class RawComponentBuilder(h.api.ComponentBuilder):
    """Adapt a raw component payload dict into a hikari component builder.

    ``build()`` returns the parsed dict verbatim (with no attachments); ``type`` is read
    back from the payload so :meth:`hikari.api.RESTClient.create_message` auto-sets the
    ``IS_COMPONENTS_V2`` flag for V2 component types (container, section, …).
    """

    __slots__ = ("_payload",)

    def __init__(self, payload: t.Mapping[str, t.Any]) -> None:
        self._payload = dict(payload)

    @property
    def type(self) -> int:
        return int(self._payload.get("type", 0))

    @property
    def id(self) -> h.UndefinedOr[int]:
        return self._payload.get("id", h.UNDEFINED)

    def build(
        self,
    ) -> tuple[
        t.MutableMapping[str, t.Any],
        t.Sequence[h.files.Resource[h.files.AsyncReader]],
    ]:
        return dict(self._payload), ()


async def fetch_raw_message_components(
    channel_id: int, message_id: int
) -> list[dict[str, t.Any]]:
    """Fetch a message's raw ``components`` array straight from the Discord REST API.

    Bypasses hikari's model deserialization so every component type round-trips
    verbatim. Authenticates as the anchor bot.
    """
    url = f"{_DISCORD_API_BASE}/channels/{int(channel_id)}/messages/{int(message_id)}"
    headers = {"Authorization": f"Bot {cfg.discord_token_anchor}"}
    async with (
        aiohttp.ClientSession() as session,
        session.get(url, headers=headers) as resp,
    ):
        resp.raise_for_status()
        data = await resp.json()
    return data.get("components") or []
