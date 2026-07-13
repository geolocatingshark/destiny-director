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

# The rotation-editor route handlers for legacy types: the edit page embeds the stored
# document (so the client form has data to render), preview renders, and save persists.

import json
import typing as t

import aiohttp.web
import pytest

from dd.anchor.extensions import rotation_editor as editor
from dd.common import schemas

pytestmark = pytest.mark.asyncio


class _FakeRequest:
    def __init__(self, query=None, body=None):
        self.query = query or {}
        self.cookies: dict[str, str] = {}
        self.headers: dict[str, str] = {}
        self._body = body

    async def json(self) -> t.Any:
        return self._body


def _req(query=None, body=None) -> aiohttp.web.Request:
    return t.cast(aiohttp.web.Request, _FakeRequest(query=query, body=body))


async def test_edit_get_embeds_stored_legacy_doc():
    # A single-activity doc keeps the test independent of the full neomuna spec.
    slug = "legacy_pale_heart"
    doc = {
        "version": 1,
        "reference_date": "2026-04-21",
        "activities": [
            {
                "key": "overthrow",
                "title": "Overthrow (Matchmade)",
                "cadence": "daily",
                "elements": [
                    {"name": "location", "values": ["The Landing", "The Impasse"]}
                ],
            }
        ],
    }
    await schemas.RotationData.set_data(slug, doc)
    resp = await editor._handle_edit_get(_req(query={"type": slug}))
    assert resp.status == 200
    # The document is embedded in the page's bootstrap for the client form to render.
    assert "The Landing" in (resp.text or "")
    assert "2026-04-21" in (resp.text or "")


async def test_edit_get_falls_back_to_default_doc_when_absent():
    resp = await editor._handle_edit_get(_req(query={"type": "legacy_europa"}))
    assert resp.status == 200
    # No stored row → the empty-but-structured scaffold (has the activity keys).
    assert "eclipsed_zone" in (resp.text or "")


async def test_preview_renders_legacy_document():
    resp = await editor._handle_preview(
        _req(body={"type": "legacy_neomuna", "data": _full_neomuna()})
    )
    assert resp.status == 200
    assert "Terminal Overload" in (resp.text or "")


async def test_save_persists_legacy_document():
    doc = _full_neomuna()
    resp = await editor._handle_edit_post(
        _req(body={"type": "legacy_neomuna", "data": doc})
    )
    assert resp.status == 200
    stored = await schemas.RotationData.get_data("legacy_neomuna")
    assert stored == doc


async def test_save_rejects_invalid_legacy_document():
    # Use an otherwise-untouched slug so "nothing written" is unambiguous.
    bad = {
        "version": 1,
        "reference_date": "not-a-date",
        "activities": [
            {
                "key": "story_mission",
                "title": "Weekly Story Mission",
                "cadence": "weekly",
                "elements": [
                    {"name": "fabled", "values": ["Commencement"]},
                    {"name": "legendary", "values": ["Charge"]},
                ],
            }
        ],
    }
    resp = await editor._handle_edit_post(
        _req(body={"type": "legacy_kepler", "data": bad})
    )
    assert resp.status == 400
    assert await schemas.RotationData.get_data("legacy_kepler") is None


async def test_save_and_preview_set_based_dares():
    doc = {
        "version": 1,
        "reference_date": "2026-04-21",
        "activities": [
            {
                "key": "rounds",
                "title": "Encounter Rounds",
                "cadence": "weekly",
                "elements": [
                    {"name": "first", "values": ["Fallen"]},
                    {"name": "second", "values": ["Hive"]},
                    {"name": "final", "values": ["Zydron (Vex)"]},
                ],
            },
            {
                "key": "loot_table",
                "title": "Legendary Loot",
                "cadence": "weekly",
                "kind": "sets",
                "schedule": ["Set 1"],
                "sets": [
                    {
                        "name": "Set 1",
                        "weapons": ["Enigmas's Draw (Sidearm)"],
                        "armor": ["Wild Hunt", "Scatterhorn"],
                    }
                ],
            },
        ],
    }
    save = await editor._handle_edit_post(
        _req(body={"type": "legacy_dares", "data": doc})
    )
    assert save.status == 200
    assert await schemas.RotationData.get_data("legacy_dares") == doc

    preview = await editor._handle_preview(
        _req(body={"type": "legacy_dares", "data": doc})
    )
    assert preview.status == 200
    body = preview.text or ""
    assert "Set 1" in body
    assert "Wild Hunt, Scatterhorn (all classes)" in body


def _full_neomuna() -> dict[str, t.Any]:
    # Matches the neomuna spec's four activities/elements exactly (schema pins them).
    return json.loads(
        json.dumps(
            {
                "version": 1,
                "reference_date": "2026-04-21",
                "activities": [
                    {
                        "key": "vex_incursion",
                        "title": "Vex Incursion Zone",
                        "cadence": "weekly",
                        "elements": [{"name": "zone", "values": ["Ahimsa Park"]}],
                    },
                    {
                        "key": "story_mission",
                        "title": "Story Mission",
                        "cadence": "weekly",
                        "elements": [{"name": "mission", "values": ["Downfall"]}],
                    },
                    {
                        "key": "partition",
                        "title": "Partition",
                        "cadence": "weekly",
                        "elements": [{"name": "variant", "values": ["Ordnance"]}],
                    },
                    {
                        "key": "terminal_overload",
                        "title": "Terminal Overload",
                        "cadence": "daily",
                        "elements": [
                            {"name": "weapon", "values": ["Synchronic Roulette"]},
                            {"name": "location", "values": ["Liming Harbor"]},
                        ],
                    },
                ],
            }
        )
    )


async def test_search_endpoint_returns_matches(monkeypatch):
    from dd.anchor.extensions.bungie_api import item_index

    monkeypatch.setattr(
        item_index,
        "search",
        lambda q, kind=None: [
            {
                "name": "Chroma Rush",
                "type": "Auto Rifle",
                "hash": 1,
                "url": "u",
                "icon": "i",
            }
        ],
    )
    resp = await editor._handle_search(_req(query={"q": "chroma", "kind": "weapon"}))
    assert resp.status == 200
    assert json.loads(resp.text or "[]")[0]["name"] == "Chroma Rush"
