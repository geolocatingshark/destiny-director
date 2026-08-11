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

"""The sqlite -> projection-row builders. No database, no network."""

from __future__ import annotations

import json
import sqlite3
import typing as t
from pathlib import Path

from dd.common import schemas
from dd.manifest_ingest import project
from dd.manifest_ingest.tests.fake_manifest import (
    BIG_HASH,
    SEASON_NUMBER,
    write_manifest,
)


def _rows(path: Path, model: type[t.Any]) -> dict[int, dict[str, t.Any]]:
    """Every projected row for ``model``, keyed by hash."""
    out: dict[int, dict[str, t.Any]] = {}
    for got_model, batch in project.iter_batches(str(path), version_id=7):
        if got_model is model:
            out |= {row["hash"]: row for row in batch}
    return out


def test_every_row_carries_the_version_id(manifest_sqlite: Path) -> None:
    for _model, batch in project.iter_batches(str(manifest_sqlite), version_id=7):
        assert {row["version_id"] for row in batch} == {7}


def test_item_columns_are_the_read_surface(manifest_sqlite: Path) -> None:
    item = _rows(manifest_sqlite, schemas.ManifestItem)[1]
    assert item["name"] == "Chroma Rush"
    assert item["name_lower"] == "chroma rush"
    assert item["icon"] == "/chroma.png"
    assert item["item_type"] == 3
    assert item["item_type_display_name"] == "Auto Rifle"
    assert item["tier_type_name"] == "Legendary"
    assert item["class_type"] == 3
    assert item["bucket_type_hash"] == 700
    assert item["collectible_hash"] == 500
    assert item["trait_ids"] == ["weapon.auto_rifle"]
    assert item["redacted"] is False


def test_hashes_stay_unsigned(manifest_sqlite: Path) -> None:
    """The stored id wraps negative above 2**31; the projected hash must not.

    This is the whole reason ``_to_signed_id`` can be deleted: nothing downstream ever
    sees the sqlite's storage representation again.
    """
    items = _rows(manifest_sqlite, schemas.ManifestItem)
    assert BIG_HASH in items
    assert all(h > 0 for h in items)


def test_season_number_is_resolved_at_ingest(manifest_sqlite: Path) -> None:
    items = _rows(manifest_sqlite, schemas.ManifestItem)
    assert items[1]["season_number"] == SEASON_NUMBER
    # No seasonHash at all -> no number, rather than a fabricated one.
    assert items[2]["season_number"] is None


def test_raw_is_kept_for_weapons_and_armour_only(manifest_sqlite: Path) -> None:
    items = _rows(manifest_sqlite, schemas.ManifestItem)
    assert items[1]["raw"]["displayProperties"]["name"] == "Chroma Rush"  # weapon
    assert items[2]["raw"] is not None  # armour
    assert items[3]["raw"] is None  # currency
    assert items[BIG_HASH]["raw"] is None  # ornament


def test_non_weapon_items_are_still_projected(manifest_sqlite: Path) -> None:
    """Currencies and cosmetics have no ``raw`` but must have rows.

    The Eververse post prices against currency names and reads ``traitIds`` and
    ``description`` off ornaments; a weapons-and-armour-only table would have dropped
    both silently.
    """
    items = _rows(manifest_sqlite, schemas.ManifestItem)
    assert items[3]["name"] == "Bright Dust"
    assert items[BIG_HASH]["trait_ids"] == ["item.ornament.weapon"]
    assert "Hawkmoon" in items[BIG_HASH]["description"]


def test_unnamed_and_redacted_rows_survive_with_defaults(manifest_sqlite: Path) -> None:
    """Filtering is the reader's job, not the projection's — but nothing may be null."""
    item = _rows(manifest_sqlite, schemas.ManifestItem)[5]
    assert item["name"] == ""
    assert item["name_lower"] == ""
    assert item["redacted"] is True


def test_the_other_tables_project(manifest_sqlite: Path) -> None:
    assert _rows(manifest_sqlite, schemas.ManifestPerk)[10] == {
        "hash": 10,
        "name": "Rampage",
        "icon": "/perk.png",
        "version_id": 7,
    }
    assert _rows(manifest_sqlite, schemas.ManifestStat)[20]["name"] == "Impact"
    assert (
        _rows(manifest_sqlite, schemas.ManifestEquipmentSlot)[700]["name"]
        == "Kinetic Weapons"
    )
    assert (
        _rows(manifest_sqlite, schemas.ManifestDestination)[800]["name"] == "The Tower"
    )
    assert (
        _rows(manifest_sqlite, schemas.ManifestPresentationNode)[600]["name"]
        == "Wild Hunt Suit"
    )


def test_collectible_keeps_its_parent_nodes(manifest_sqlite: Path) -> None:
    collectible = _rows(manifest_sqlite, schemas.ManifestCollectible)[500]
    assert collectible["item_hash"] == 1
    assert collectible["parent_node_hashes"] == [600]


def test_vendor_keeps_its_identifier_and_location_order(manifest_sqlite: Path) -> None:
    """``locations`` is indexed into by the vendor API, so its order is its meaning."""
    vendor = _rows(manifest_sqlite, schemas.ManifestVendor)[2190858386]
    assert vendor["vendor_identifier"] == "XUR"
    assert vendor["locations"] == [{"destinationHash": 800}]


def test_activity_keeps_its_classification_inputs(manifest_sqlite: Path) -> None:
    """The classification stays in weekly_reset; the ingest only carries its inputs."""
    activity = _rows(manifest_sqlite, schemas.ManifestActivity)[400]
    assert activity["name"] == "The Corrupted"
    assert activity["activity_type_hash"] == 300
    assert activity["mode_types"] == [18]
    assert activity["direct_mode_type"] == 18
    assert activity["max_party"] == 3
    assert _rows(manifest_sqlite, schemas.ManifestActivityType)[300]["name"] == "Strike"


def test_a_missing_table_is_projected_as_nothing(tmp_path: Path) -> None:
    """Bungie dropping a table must not abort an otherwise-good ingest."""
    path = write_manifest(
        tmp_path / "sparse.content",
        {"DestinyStatDefinition": [{"hash": 20, "displayProperties": {"name": "Imp"}}]},
    )
    projected = {model for model, _rows_ in project.iter_batches(str(path), 1)}
    assert projected == {schemas.ManifestStat}


def test_a_malformed_row_is_skipped_not_fatal(tmp_path: Path) -> None:
    """One bad blob must not take the whole (all-or-nothing) transaction down."""
    path = tmp_path / "corrupt.content"
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE "DestinyStatDefinition" (id INTEGER PRIMARY KEY, json)')
    con.executemany(
        'INSERT INTO "DestinyStatDefinition" (id, json) VALUES (?, ?)',
        [
            (20, json.dumps({"hash": 20, "displayProperties": {"name": "Impact"}})),
            (21, "{not json"),
            (22, json.dumps({"displayProperties": {"name": "No hash"}})),
        ],
    )
    con.commit()
    con.close()

    assert list(_rows(path, schemas.ManifestStat)) == [20]


def test_batches_are_bounded(tmp_path: Path) -> None:
    """Streaming, not one list: the item table is why this exists."""
    path = write_manifest(
        tmp_path / "many.content",
        {
            "DestinyStatDefinition": [
                {"hash": h, "displayProperties": {"name": f"s{h}"}}
                for h in range(project.BATCH_SIZE * 2 + 3)
            ]
        },
    )
    sizes = [len(batch) for _model, batch in project.iter_batches(str(path), 1)]
    assert sizes == [project.BATCH_SIZE, project.BATCH_SIZE, 3]
