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

"""JSON Schemas for rotation-based posts (``lost_sector``, …).

One schema per post type is the single source of truth for **both** the json-editor
web form *and* server-side validation. The UI-only keywords (``options``, ``watch``,
``enumSource``, ``headerTemplate``) are ignored by the validator, so the same document
that the form produces validates on the server.

The vocab constants below are also imported by
:mod:`dd.sector_accounting.sector_accounting` (``Rotation.from_json``/``to_json``) so
the form, the validator and the domain mapping can never drift apart.
"""

from __future__ import annotations

import typing as t

import fastjsonschema

# --- shared vocab -----------------------------------------------------------------

CHAMPION_TYPES = ["Barrier", "Overload", "Unstoppable"]
SHIELD_ELEMENTS = ["Arc", "Void", "Solar", "Stasis", "Strand"]
# The nine destinations, each an independent daily cycle.
LOST_SECTOR_ZONES = [
    "Cosmodrome",
    "Dreaming City",
    "EDZ",
    "Europa",
    "Moon",
    "Neomuna",
    "Nessus",
    "Pale Heart",
    "Throne World",
]


def _build_lost_sector_schema() -> dict[str, t.Any]:
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "title": "Lost sector rotation",
        "required": ["reference_date", "schedule", "sectors"],
        "additionalProperties": False,
        "properties": {
            "version": {"type": "integer", "options": {"hidden": True}},
            "reference_date": {
                "type": "string",
                "format": "date",
                "title": "Rotation start date",
            },
            "schedule": {
                "type": "object",
                "title": "Daily schedule (per destination)",
                "required": list(LOST_SECTOR_ZONES),
                "additionalProperties": False,
                "properties": {
                    zone: {"$ref": "#/definitions/zoneCycle"}
                    for zone in LOST_SECTOR_ZONES
                },
            },
            "sectors": {
                "type": "array",
                "format": "tabs",
                "title": "Sectors",
                "headerTemplate": "{{ self.name }}",
                "items": {
                    "type": "object",
                    "title": "Sector",
                    "required": ["name", "shortlink_gfx", "expert", "master"],
                    "properties": {
                        "name": {"type": "string", "title": "Name"},
                        "shortlink_gfx": {
                            "type": "string",
                            "format": "uri",
                            "title": "Graphic link",
                        },
                        "expert": {
                            "$ref": "#/definitions/difficulty",
                            "title": "Expert",
                        },
                        "master": {
                            "$ref": "#/definitions/difficulty",
                            "title": "Master",
                        },
                    },
                },
            },
        },
        "definitions": {
            "zoneCycle": {
                "type": "array",
                "title": "Daily sectors",
                "items": {
                    "type": "string",
                    # UI-only: populate the dropdown from the sector list (typo
                    # safety without a hard enum). Ignored by the validator.
                    "watch": {"secs": "sectors"},
                    "enumSource": [{"source": "secs", "value": "{{ item.name }}"}],
                },
            },
            "difficulty": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "champions": {
                        "type": "array",
                        "format": "checkbox",
                        "uniqueItems": True,
                        "items": {"type": "string", "enum": list(CHAMPION_TYPES)},
                    },
                    "shields": {
                        "type": "array",
                        "format": "checkbox",
                        "uniqueItems": True,
                        "items": {"type": "string", "enum": list(SHIELD_ELEMENTS)},
                    },
                },
            },
        },
    }


LOST_SECTOR_SCHEMA: dict[str, t.Any] = _build_lost_sector_schema()

# Registry keyed by post-type slug (matches AutoPostSettings.name / cfg.followables).
ROTATION_SCHEMAS: dict[str, dict[str, t.Any]] = {
    "lost_sector": LOST_SECTOR_SCHEMA,
}

_compiled_validators: dict[str, t.Callable[[t.Any], t.Any]] = {}

# json-editor uses ``format`` for widget selection (e.g. ``checkbox`` array pickers,
# ``tabs`` sector cards). fastjsonschema *raises* on a format it doesn't recognise
# (unlike the keywords it ignores), so register the UI-only ones as no-op validators —
# keeping the standard ``date``/``uri`` formats genuinely validated. New UI formats
# added to a schema must be listed here too.
_UI_ONLY_FORMATS: dict[str, t.Callable[[t.Any], bool]] = {
    "checkbox": lambda _value: True,
    "tabs": lambda _value: True,
}


def get_schema(post_type: str) -> dict[str, t.Any]:
    """Return the JSON Schema for ``post_type`` (raises ``KeyError`` if unknown)."""
    return ROTATION_SCHEMAS[post_type]


def validate(post_type: str, data: t.Any) -> None:
    """Validate ``data`` against ``post_type``'s schema.

    Raises :class:`fastjsonschema.JsonSchemaException` on invalid input and
    ``KeyError`` for an unknown post type. Compiled validators are cached per type.
    """
    validator = _compiled_validators.get(post_type)
    if validator is None:
        validator = fastjsonschema.compile(
            get_schema(post_type), formats=_UI_ONLY_FORMATS
        )
        _compiled_validators[post_type] = validator
    validator(data)
