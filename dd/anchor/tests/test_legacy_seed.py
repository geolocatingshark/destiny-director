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

# Committed legacy seed-data integrity + the web-editor's per-type dispatch branches.

import datetime as dt
import json
import pathlib

import pytest

from dd.anchor.extensions import rotation_editor as editor
from dd.common import rotation_schema as rs
from dd.sector_accounting.legacy_activities import LegacyRotation

_SEED_DIR = (
    pathlib.Path(editor.__file__).resolve().parent.parent / "seed_data" / "legacy"
)

# Destinations that contain a weekly activity need a Tuesday reference date for the
# ``days // 7`` week boundary to align with the real reset.
_WEEKLY_BEARING = {
    key
    for key, (_title, activities) in rs.LEGACY_DESTINATIONS.items()
    if any(cadence == "weekly" for _k, _t, cadence, _f in activities)
}


@pytest.mark.parametrize("key", sorted(rs.LEGACY_DESTINATIONS))
def test_seed_doc_exists_validates_and_builds(key: str):
    doc = json.loads((_SEED_DIR / f"{key}.json").read_text(encoding="utf-8"))
    rs.validate(f"legacy_{key}", doc)
    LegacyRotation.from_json(doc)  # hard gate: must build


@pytest.mark.parametrize("key", sorted(_WEEKLY_BEARING))
def test_weekly_seed_reference_is_a_tuesday(key: str):
    doc = json.loads((_SEED_DIR / f"{key}.json").read_text(encoding="utf-8"))
    ref = dt.date.fromisoformat(doc["reference_date"])
    assert ref.weekday() == 1, f"{key} reference_date must be a Tuesday"


def test_seed_spotcheck_known_dates():
    # Neomuna story mission on a real sheet week and Kepler's fabled mission.
    neo = LegacyRotation.from_json(
        json.loads((_SEED_DIR / "neomuna.json").read_text(encoding="utf-8"))
    )
    date = dt.datetime(2026, 7, 14, 18, tzinfo=dt.UTC)
    resolved = {r.key: r.values for r in neo(date)}
    assert resolved["story_mission"]["mission"] == "Downfall"

    kep = LegacyRotation.from_json(
        json.loads((_SEED_DIR / "kepler.json").read_text(encoding="utf-8"))
    )
    date = dt.datetime(2026, 4, 21, 18, tzinfo=dt.UTC)
    resolved = {r.key: r.values for r in kep(date)}
    assert resolved["story_mission"]["fabled"] == "Commencement"


def test_editor_default_doc_for_legacy_type():
    doc = editor._default_doc("legacy_dares")
    assert [a["key"] for a in doc["activities"]] == ["rounds", "loot_table"]
    rounds, loot = doc["activities"]
    # Element-based activity scaffolds empty value lists...
    assert all(e["values"] == [] for e in rounds["elements"])
    # ...and the set-based loot scaffolds an empty schedule + set pool.
    assert loot["kind"] == "sets"
    assert loot["schedule"] == [] and loot["sets"] == []


def test_dares_seed_is_set_based_and_spotchecks():
    doc = json.loads((_SEED_DIR / "dares.json").read_text(encoding="utf-8"))
    rot = LegacyRotation.from_json(doc)
    # Sheet week 2025-12-16 was Set 3.
    date = dt.datetime(2025, 12, 16, 18, tzinfo=dt.UTC)
    loot = {r.key: r for r in rot(date)}["loot_table"]
    assert loot.set is not None
    assert loot.set.name == "Set 3"
    assert loot.set.weapons and loot.set.armor


def test_editor_builds_and_previews_legacy_type():
    doc = json.loads((_SEED_DIR / "throne_world.json").read_text(encoding="utf-8"))
    obj = editor._build_domain_object("legacy_throne_world", doc)
    assert isinstance(obj, LegacyRotation)
    html = editor._render_preview("legacy_throne_world", obj)
    assert "Wellspring" in html


def test_home_page_lists_legacy_slugs():
    html = editor._render_home_html()
    assert "legacy_neomuna" in html
    assert "legacy_kepler" in html
