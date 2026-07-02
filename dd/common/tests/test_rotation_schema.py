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

# Server-side validation of the rotation JSON schema (fastjsonschema). The same schema
# drives the json-editor form; here we only assert the validator accepts/rejects.

import typing as t

import fastjsonschema
import pytest

from dd.common import rotation_schema as rs


def _doc(**overrides: t.Any) -> dict[str, t.Any]:
    doc: dict[str, t.Any] = {
        "version": 1,
        "reference_date": "2023-07-20",
        "schedule": {z: ["Alpha"] for z in rs.LOST_SECTOR_ZONES},
        "sectors": [
            {
                "name": "Alpha",
                "shortlink_gfx": "https://kyber3000.com/x",
                "expert": {"champions": ["Barrier"], "shields": ["Arc"]},
                "master": {"champions": ["Overload"], "shields": ["Void"]},
            }
        ],
    }
    doc.update(overrides)
    return doc


def test_valid_document_passes():
    rs.validate("lost_sector", _doc())


def test_ui_only_format_keywords_do_not_break_compilation():
    # checkbox/tabs are json-editor widget hints; registered as no-op server formats.
    rs.validate("lost_sector", _doc())  # would raise at compile if unhandled


@pytest.mark.parametrize(
    "doc",
    [
        _doc(reference_date="not-a-date"),
        _doc(sectors=[{"name": "X"}]),  # sector missing required fields
        _doc(schedule={"Cosmodrome": ["A"]}),  # missing the other eight zones
        _doc(extra_top_level_key=123),  # additionalProperties: false
    ],
)
def test_invalid_documents_rejected(doc: dict[str, t.Any]):
    with pytest.raises(fastjsonschema.JsonSchemaException):
        rs.validate("lost_sector", doc)


def test_bad_uri_rejected():
    bad = _doc()
    bad["sectors"][0]["shortlink_gfx"] = "not a url"
    with pytest.raises(fastjsonschema.JsonSchemaException):
        rs.validate("lost_sector", bad)


def test_unknown_post_type_raises_keyerror():
    with pytest.raises(KeyError):
        rs.validate("does_not_exist", _doc())


# --- xur_location ----------------------------------------------------------------


def _xur_doc(**overrides: t.Any) -> dict[str, t.Any]:
    doc: dict[str, t.Any] = {
        "version": 1,
        "locations": [
            {
                "api_location_name": "Nessus, Watcher's Grave",
                "friendly_location_name": "Watcher's Grave, Nessus",
                "link": "https://kyber3000.com/x",
            }
        ],
    }
    doc.update(overrides)
    return doc


def test_xur_valid_document_passes():
    rs.validate("xur_location", _xur_doc())


def test_xur_minimal_location_passes():
    # Only api_location_name is required; friendly name / link are optional.
    rs.validate(
        "xur_location", {"version": 1, "locations": [{"api_location_name": "x"}]}
    )


@pytest.mark.parametrize(
    "doc",
    [
        {"version": 1},  # missing required "locations"
        {"version": 1, "locations": [{}]},  # location missing api_location_name
        # additionalProperties: false on a location item
        {"version": 1, "locations": [{"api_location_name": "x", "extra": 1}]},
    ],
)
def test_xur_invalid_documents_rejected(doc: dict[str, t.Any]):
    with pytest.raises(fastjsonschema.JsonSchemaException):
        rs.validate("xur_location", doc)


def test_xur_bad_uri_rejected():
    with pytest.raises(fastjsonschema.JsonSchemaException):
        rs.validate(
            "xur_location",
            {
                "version": 1,
                "locations": [{"api_location_name": "x", "link": "not a url"}],
            },
        )
