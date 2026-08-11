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

"""The ingest end to end, against the test database. Bungie is faked, not reached.

What these pin is the contract every reader depends on: exactly one current version, a
flip that is all-or-nothing, and a failure that leaves the previous version serving.
"""

from __future__ import annotations

import typing as t
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from dd.common import schemas
from dd.manifest_ingest import (
    bungie,
    ingest as ingest_module,
)
from dd.manifest_ingest.tests.fake_manifest import write_manifest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest_asyncio.fixture(autouse=True)
async def _clean_manifest_tables() -> t.AsyncIterator[None]:
    """Each test starts with an empty projection.

    Every table is cleared by name rather than trusting the FK cascade: SQLite does not
    enforce foreign keys unless asked to, so a version-only delete would leave orphaned
    rows here — and since SQLite reuses ids after a delete, the next test's version
    would collide with them.
    """
    async with schemas.db_session() as session:
        for model in schemas.MANIFEST_PROJECTION_TABLES:
            await session.execute(delete(model))
        await session.execute(delete(schemas.ManifestVersion))
        await session.commit()
    yield


@pytest.fixture
def fake_bungie(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Point the ingest at a local fake manifest, and let a test drive its version.

    Returns a setter; call it with a version string to say what Bungie now reports.
    """
    state = {"version": "v1"}

    async def current_version(_api_key: str) -> tuple[str, str]:
        return state["version"], "/common/world_content.zip"

    async def fetch_world_content(_fragment: str, into: Path) -> Path:
        return write_manifest(into / "world.content")

    monkeypatch.setattr(bungie, "current_version", current_version)
    monkeypatch.setattr(bungie, "fetch_world_content", fetch_world_content)
    return lambda version: state.__setitem__("version", version)


async def _row_count(model: type[t.Any]) -> int:
    async with schemas.db_session() as session:
        return int(
            (
                await session.execute(select(func.count()).select_from(model))
            ).scalar_one()
        )


async def _versions() -> list[tuple[str, bool]]:
    async with schemas.db_session() as session:
        rows = (
            await session.execute(
                select(
                    schemas.ManifestVersion.version, schemas.ManifestVersion.is_current
                ).order_by(schemas.ManifestVersion.id)
            )
        ).all()
    return [(str(v), bool(c)) for v, c in rows]


async def test_first_ingest_writes_and_becomes_current(fake_bungie) -> None:
    assert await ingest_module.ingest("key") is True

    assert await _versions() == [("v1", True)]
    assert await _row_count(schemas.ManifestItem) == 5
    assert await _row_count(schemas.ManifestVendor) == 3
    assert await _row_count(schemas.ManifestActivity) == 1


async def test_an_unchanged_version_is_the_fast_path(fake_bungie, monkeypatch) -> None:
    """The hourly tick must not download anything when the version has not moved."""
    await ingest_module.ingest("key")

    async def explode(*_args, **_kwargs):
        raise AssertionError("the fast path must not download the manifest")

    monkeypatch.setattr(bungie, "fetch_world_content", explode)
    assert await ingest_module.ingest("key") is False
    assert await _versions() == [("v1", True)]


async def test_a_new_version_flips_and_keeps_exactly_two(fake_bungie) -> None:
    await ingest_module.ingest("key")
    fake_bungie("v2")
    await ingest_module.ingest("key")

    assert await _versions() == [("v1", False), ("v2", True)]

    fake_bungie("v3")
    await ingest_module.ingest("key")
    # v1 is gone; rolling back to v2 is still one UPDATE away.
    assert await _versions() == [("v2", False), ("v3", True)]


async def test_superseded_projection_rows_are_deleted(fake_bungie) -> None:
    """Retention drops versions; their projection rows must not outlive them.

    Asserted on the ids rather than on the cascade: the ingest deletes the rows itself
    precisely so this holds on a backend with foreign keys switched off.
    """
    for version in ("v1", "v2", "v3"):
        fake_bungie(version)
        await ingest_module.ingest("key")

    async with schemas.db_session() as session:
        live = set(
            (await session.execute(select(schemas.ManifestVersion.id))).scalars()
        )
        referenced = set(
            (await session.execute(select(schemas.ManifestItem.version_id))).scalars()
        )
    assert referenced <= live
    assert await _row_count(schemas.ManifestItem) == 5 * len(live)


async def test_a_failed_ingest_leaves_the_old_version_serving(
    fake_bungie, monkeypatch
) -> None:
    """The whole point of the single transaction: a bad run is a non-event."""
    await ingest_module.ingest("key")
    before = await _versions()

    fake_bungie("v2")
    real_iter_batches = ingest_module.project.iter_batches

    def explode_partway(path: str, version_id: int):
        # Yield one good batch first, so the failure lands *mid-write* rather than
        # before anything was inserted — that is the case a non-transactional ingest
        # would leave half-projected.
        for index, batch in enumerate(real_iter_batches(path, version_id)):
            if index >= 1:
                raise RuntimeError("boom")
            yield batch

    monkeypatch.setattr(ingest_module.project, "iter_batches", explode_partway)

    with pytest.raises(RuntimeError, match="boom"):
        await ingest_module.ingest("key")

    assert await _versions() == before
    assert await _row_count(schemas.ManifestItem) == 5


async def test_run_reports_failure_and_alerts(monkeypatch) -> None:
    """A failing tick exits non-zero (so the platform sees it) and posts an alert."""
    posted: list[str] = []

    async def boom(_api_key: str) -> bool:
        raise bungie.ManifestUnavailable("bungie is down")

    monkeypatch.setattr(ingest_module, "ingest", boom)
    monkeypatch.setattr(ingest_module, "alert", lambda msg: _record(posted, msg))
    monkeypatch.setattr(schemas.cfg, "bungie_api_key", "key")

    assert await ingest_module.run() == 1
    assert "bungie is down" in posted[0]


async def test_run_without_an_api_key_is_a_quiet_success(monkeypatch) -> None:
    """A keyless environment is configuration, not breakage — do not alert hourly."""
    posted: list[str] = []
    monkeypatch.setattr(schemas.cfg, "bungie_api_key", "")
    monkeypatch.setattr(ingest_module, "alert", lambda msg: _record(posted, msg))

    assert await ingest_module.run() == 0
    assert posted == []


async def _record(sink: list[str], message: str) -> None:
    sink.append(message)
