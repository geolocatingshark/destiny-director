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


def _build_xur_location_schema() -> dict[str, t.Any]:
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "title": "Xûr location map",
        "required": ["locations"],
        "additionalProperties": False,
        "properties": {
            "version": {"type": "integer", "options": {"hidden": True}},
            "locations": {
                "type": "array",
                "title": "Locations",
                "items": {
                    "type": "object",
                    "title": "Location",
                    # Only the API name is required; a missing friendly name / link
                    # renders as the raw API name (XurLocation.__str__ handles it).
                    "required": ["api_location_name"],
                    "additionalProperties": False,
                    "properties": {
                        "api_location_name": {
                            "type": "string",
                            "title": "API location name",
                        },
                        "friendly_location_name": {
                            "type": "string",
                            "title": "Friendly location name",
                        },
                        "link": {
                            "type": "string",
                            "format": "uri",
                            "title": "Link",
                        },
                    },
                },
            },
        },
    }


XUR_LOCATION_SCHEMA: dict[str, t.Any] = _build_xur_location_schema()


# --- legacy world-activity rotations ----------------------------------------------
#
# Each Destiny "legacy" destination (Neomuna, the Moon, Dares of Eternity, …) is one
# rotation document made of independent *activities*. Each activity is a set of
# *elements* (weapon, location, boss, …), and **every element is its own independent,
# variable-length cyclic list** of values — so a 2-cycle mode can sit beside a 4-cycle
# boss, or a Dares weapon slot running 12 unique weeks beside an armor slot on a 3-week
# cycle. One shared schema *builder* + a per-destination *spec* gives DRY code but a
# precise form per destination. The document is self-describing (each activity carries
# its ``key``/``title``/``cadence`` as hidden constants) so the domain model
# (:mod:`dd.sector_accounting.legacy_activities`) never needs the spec.

# An activity is ``(key, title, cadence, elements)`` where ``elements`` is either a list
# of element names (element-based) or the literal ``"sets"`` (set-based, e.g. Dares
# loot). A spec is ``(title, [activity, ...])``.
_Activity = tuple[str, str, t.Literal["daily", "weekly"], list[str] | t.Literal["sets"]]
_DestinationSpec = tuple[str, list[_Activity]]

LEGACY_DESTINATIONS: dict[str, _DestinationSpec] = {
    "neomuna": (
        "Neomuna",
        [
            ("vex_incursion", "Vex Incursion Zone", "weekly", ["zone"]),
            ("story_mission", "Story Mission", "weekly", ["mission"]),
            ("partition", "Partition", "weekly", ["variant"]),
            ("terminal_overload", "Terminal Overload", "daily", ["weapon", "location"]),
        ],
    ),
    "moon": (
        "The Moon",
        [
            (
                "wandering_nightmare",
                "Wandering Nightmare (Patrol)",
                "weekly",
                ["nightmare", "location"],
            ),
            (
                "nightmare_hunts",
                "Nightmare Hunts",
                "weekly",
                ["hunt_1", "hunt_2", "hunt_3"],
            ),
            ("altar_of_sorrow", "Altar of Sorrow", "daily", ["weapon", "boss"]),
        ],
    ),
    "dreaming_city": (
        "Dreaming City",
        [
            ("petras_location", "Petra's Location", "weekly", ["location"]),
            ("blind_well", "Blind Well", "weekly", ["charge"]),
            ("quest", "Weekly Quest", "weekly", ["quest"]),
            ("curse_strength", "Curse Strength", "weekly", ["strength"]),
            (
                "ascendant_challenge",
                "Ascendant Challenge",
                "weekly",
                ["challenge", "location"],
            ),
        ],
    ),
    "europa": (
        "Europa",
        [
            ("eclipsed_zone", "Eclipsed Zone", "weekly", ["zone"]),
            ("exo_challenge", "Exo Challenge", "weekly", ["challenge"]),
            ("empire_hunt", "Empire Hunt", "weekly", ["hunt"]),
        ],
    ),
    "dares": (
        "Dares of Eternity",
        [
            ("rounds", "Encounter Rounds", "weekly", ["first", "second", "final"]),
            # Set-based: 4 fixed loot sets + a weekly schedule of which set is live.
            ("loot_table", "Legendary Loot", "weekly", "sets"),
        ],
    ),
    "pale_heart": (
        "The Pale Heart",
        [
            ("overthrow", "Overthrow (Matchmade)", "daily", ["location"]),
        ],
    ),
    "throne_world": (
        "Savathûn's Throne World",
        [
            ("wellspring", "Wellspring", "daily", ["weapon", "mode", "boss"]),
            ("lucent_executioner", "Lucent Executioner", "daily", ["boss", "location"]),
        ],
    ),
    "kepler": (
        "Kepler",
        [
            (
                "story_mission",
                "Weekly Story Mission",
                "weekly",
                ["fabled", "legendary"],
            ),
        ],
    ),
    "rahool": (
        "Rahool",
        [
            ("rahool_focus", "Rahool's Armor Focus", "daily", ["slot"]),
        ],
    ),
}


def _legacy_field_label(name: str) -> str:
    return name.replace("_", " ").title()


def _legacy_element_schema(name: str) -> dict[str, t.Any]:
    label = _legacy_field_label(name)
    return {
        "type": "object",
        "title": label,
        "required": ["name", "values"],
        "additionalProperties": False,
        "properties": {
            # The element name is fixed by the spec (hidden const); only ``values`` —
            # the element's independent cycle — is edited.
            "name": {"type": "string", "const": name, "options": {"hidden": True}},
            "values": {
                "type": "array",
                "title": f"{label} rotation",
                "items": {"type": "string"},
            },
        },
    }


def _legacy_sets_activity_schema(
    key: str, title: str, cadence: str
) -> dict[str, t.Any]:
    """Schema for a set-based activity: a set pool + a weekly schedule of set ids."""
    return {
        "type": "object",
        "title": title,
        "required": ["key", "title", "cadence", "kind", "schedule", "sets"],
        "additionalProperties": False,
        "properties": {
            "key": {"type": "string", "const": key, "options": {"hidden": True}},
            "title": {"type": "string", "const": title, "options": {"hidden": True}},
            "cadence": {
                "type": "string",
                "const": cadence,
                "options": {"hidden": True},
            },
            "kind": {"type": "string", "const": "sets", "options": {"hidden": True}},
            "schedule": {
                "type": "array",
                "title": "Weekly schedule (set names, in order, looping)",
                "items": {"type": "string"},
            },
            "sets": {
                "type": "array",
                "title": "Sets",
                "format": "tabs",
                "headerTemplate": "{{ self.name }}",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "title": "Set",
                    "required": ["name", "weapons", "armor"],
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string", "title": "Set name"},
                        "weapons": {
                            "type": "array",
                            "title": "Weapons",
                            "items": {"type": "string"},
                        },
                        "armor": {
                            "type": "array",
                            "title": "Armor",
                            "items": {"type": "string"},
                        },
                    },
                },
            },
        },
    }


def _legacy_activity_schema(activity: _Activity) -> dict[str, t.Any]:
    key, title, cadence, element_names = activity
    if element_names == "sets":
        return _legacy_sets_activity_schema(key, title, cadence)
    return {
        "type": "object",
        "title": title,
        "required": ["key", "title", "cadence", "elements"],
        "additionalProperties": False,
        "properties": {
            # Structural fields are fixed by the spec — hidden constants so the form
            # only ever edits element values, and the stored doc stays self-describing.
            "key": {"type": "string", "const": key, "options": {"hidden": True}},
            "title": {"type": "string", "const": title, "options": {"hidden": True}},
            "cadence": {
                "type": "string",
                "const": cadence,
                "options": {"hidden": True},
            },
            # A fixed tuple: exactly these elements, in this order.
            "elements": {
                "type": "array",
                "title": f"{title} elements",
                "minItems": len(element_names),
                "maxItems": len(element_names),
                "additionalItems": False,
                "items": [_legacy_element_schema(name) for name in element_names],
            },
        },
    }


def _build_legacy_rotation_schema(spec: _DestinationSpec) -> dict[str, t.Any]:
    title, activities = spec
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "title": f"{title} (legacy)",
        "required": ["reference_date", "activities"],
        "additionalProperties": False,
        "properties": {
            "version": {"type": "integer", "options": {"hidden": True}},
            "reference_date": {
                "type": "string",
                "format": "date",
                "title": "Rotation start date (a weekly-reset Tuesday)",
            },
            # A fixed tuple: exactly these activities, in this order.
            "activities": {
                "type": "array",
                "title": "Activities",
                "minItems": len(activities),
                "maxItems": len(activities),
                "additionalItems": False,
                "items": [_legacy_activity_schema(a) for a in activities],
            },
        },
    }


def _legacy_default_activity(activity: _Activity) -> dict[str, t.Any]:
    key, title, cadence, element_names = activity
    if element_names == "sets":
        return {
            "key": key,
            "title": title,
            "cadence": cadence,
            "kind": "sets",
            "schedule": [],
            "sets": [],
        }
    return {
        "key": key,
        "title": title,
        "cadence": cadence,
        "elements": [{"name": name, "values": []} for name in element_names],
    }


def legacy_default_doc(post_type: str) -> dict[str, t.Any]:
    """An empty-but-structurally-complete scaffold for a legacy destination slug."""
    _title, activities = LEGACY_DESTINATIONS[post_type.removeprefix("legacy_")]
    return {
        "version": 1,
        "reference_date": "",
        "activities": [_legacy_default_activity(a) for a in activities],
    }


# Registry keyed by post-type slug (matches AutoPostSettings.name / cfg.followables).
ROTATION_SCHEMAS: dict[str, dict[str, t.Any]] = {
    "lost_sector": LOST_SECTOR_SCHEMA,
    "xur_location": XUR_LOCATION_SCHEMA,
    # Each legacy destination registers under its own ``legacy_<key>`` slug, so it
    # appears automatically at /rotation edit and gets its own DB row (no migration).
    **{
        f"legacy_{key}": _build_legacy_rotation_schema(spec)
        for key, spec in LEGACY_DESTINATIONS.items()
    },
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
