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

"""Build a Components V2 message from JSON exported by an external builder website.

Admins build a message visually on a Components V2 builder (e.g. message.style or
discord.builders), copy the exported JSON, and send it to the bot (the "Post components"
message command reads it from a pasted message or its attachment). The components are
sent verbatim as a bot message via a thin builder shim, so no per-component mapper has
to be maintained — hikari auto-sets the Components V2 message flag based on the
component types present.
"""

import json
import typing as t

import hikari as h


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


def parse_post_json(raw: str) -> list[RawComponentBuilder]:
    """Parse pasted message JSON into top-level component builders.

    Accepts a full message object (``{"components": [...]}``), a bare components array
    (``[...]``) or a single component object (``{"type": ...}``). Raises
    :class:`ValueError` with a user-facing message on anything malformed.
    """
    raw = raw.strip()
    if not raw:
        raise ValueError("No JSON was provided.")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"That isn't valid JSON ({e.msg}, line {e.lineno}).") from e

    if isinstance(data, dict):
        if "components" in data:
            components = data["components"]
        elif "type" in data:
            components = [data]
        else:
            raise ValueError("JSON object has no `components` array.")
    elif isinstance(data, list):
        components = data
    else:
        raise ValueError("Expected a JSON object or array.")

    if not isinstance(components, list) or not components:
        raise ValueError("`components` must be a non-empty array.")

    builders: list[RawComponentBuilder] = []
    for i, component in enumerate(components):
        if not isinstance(component, dict) or "type" not in component:
            raise ValueError(f"Component #{i + 1} is not a valid component object.")
        builders.append(RawComponentBuilder(component))
    return builders
