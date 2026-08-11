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

"""Load manifest definitions into the projection, the way production does.

A test that wants manifest data states it as Bungie's JSON and gets it into the
database through the **real** writer: a manifest sqlite, then
``dd.manifest_ingest.project``, then the same inserts the ingest runs. Hand-writing
projection rows in each test would let the reader's tests pass against a shape the
writer never produces.

Living in ``dd/anchor/tests/`` rather than a conftest because several test modules
import it directly — a sibling-module import is unambiguous, where importing from a
conftest is not.
"""

from __future__ import annotations

import datetime as dt
import typing as t
from pathlib import Path

from sqlalchemy import delete, insert, update

from dd.common import schemas
from dd.manifest_ingest import project
from dd.manifest_ingest.tests.fake_manifest import write_manifest


async def clear_projection() -> None:
    """Drop every manifest row, so a test starts from "nothing ingested"."""
    async with schemas.db_session() as session:
        for model in schemas.MANIFEST_PROJECTION_TABLES:
            await session.execute(delete(model))
        await session.execute(delete(schemas.ManifestVersion))
        await session.commit()


async def load_projection(
    definitions: dict[str, list[dict[str, t.Any]]],
    tmp_path: Path,
    *,
    version: str = "test",
    replace: bool = True,
) -> int:
    """Ingest ``definitions`` as the current manifest; returns its version id.

    ``replace=False`` **supersedes** rather than wipes: the previous version stays in
    place as not-current and a new row is added, which is what the ingest actually does
    on a manifest update. Use it to exercise a flip — wiping first would let SQLite
    hand the new version the id the old one just released, so a reader keyed on the id
    would see no change at all.
    """
    if replace:
        await clear_projection()
    else:
        async with schemas.db_session() as session:
            await session.execute(
                update(schemas.ManifestVersion)
                .where(schemas.ManifestVersion.is_current)
                .values(is_current=False)
            )
            await session.commit()
    path = write_manifest(tmp_path / f"{version}.content", definitions)

    async with schemas.db_session() as session:
        version_id = int(
            (
                await session.execute(
                    insert(schemas.ManifestVersion)
                    .values(
                        version=version,
                        ingested_at=dt.datetime.now(),
                        is_current=True,
                    )
                    .returning(schemas.ManifestVersion.id)
                )
            ).scalar_one()
        )
        for model, rows in project.iter_batches(str(path), version_id):
            await session.execute(insert(model), rows)
        await session.commit()
    return version_id
