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

"""Serialize an :class:`~dd.hmessage.HMessage` to plain JSON primitives.

The bridge between the two halves of the codebase that need to *look at* a built
message rather than send it:

- **beacon** stores the payload in the ``mirror_delivery`` ledger, so a delivered
  version can be re-rendered later (:mod:`dd.beacon.mirror_worker`).
- **anchor** renders it to safe HTML — the mirror-log viewer and the feed page's
  post preview both walk this shape via :func:`dd.anchor.cv2_render.render_snapshot`.

Lives here, in the shared message package, because it is a pure ``HMessage`` → dict
transform with no bot, DB or web dependency — and because anchor must not import from
beacon (nothing in ``dd/anchor`` does, deliberately).

Only the Components-V2 branch is shared. Serializing classic embeds needs a live
``entity_factory``, which is a bot dependency this module deliberately does not take;
beacon keeps that branch locally, alongside the ledger concerns (summary extraction,
over-cap truncation) that are meaningful only to the ledger.
"""

import enum
import json
import typing as t

from .message import HMessage


def json_primitive(obj: t.Any) -> t.Any:
    """Deep-convert a build/serialize payload to plain JSON primitives.

    hikari's ``build()`` / ``serialize_embed`` payloads carry ``IntEnum`` type tags
    (``ComponentType``, ``ButtonStyle``) and the odd ``datetime``/``Color``; round-trip
    through ``json`` with a coercing default so the result is clean primitives a
    renderer can walk without importing hikari.
    """

    def _default(o: t.Any) -> t.Any:
        if isinstance(o, enum.Enum):
            return o.value
        return str(o)

    return json.loads(json.dumps(obj, default=_default))


def cv2_payload(hmsg: HMessage) -> dict[str, t.Any]:
    """``{"components": [...]}`` — a CV2 message as raw Discord component dicts.

    Serializes the rebuilt component builders back through ``build()[0]``, which is
    exactly the shape Discord receives, so a render of this payload matches the post.
    Pairs with ``render_snapshot(payload, "cv2")`` on the anchor side.
    """
    return {"components": [json_primitive(c.build()[0]) for c in hmsg.components]}
