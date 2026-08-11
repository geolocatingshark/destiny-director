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

"""The ingest itself: check currency, and if it moved, rebuild the projection.

Runs as a Railway cron service (``python -m dd.manifest_ingest``, hourly). Almost every
tick is the fast path — one Bungie metadata call, one SELECT, exit — and the handful of
real ingests a month are the only time the download and the ~39k-row walk happen. That
cost used to be paid inside a *serving* process, where it was anchor's memory peak.

The write is **one transaction**: a new version row, all its projection rows, the
``is_current`` flip, and the retention delete either all land or none do. A failure
anywhere leaves the previous version serving untouched — which is what makes the
database the manifest's fallback and a bad ingest a non-event.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import tempfile
import typing as t
from pathlib import Path

import aiohttp
from sqlalchemy import delete, func, insert, select, update

from dd.common import cfg, schemas

from . import bungie, project

logger = logging.getLogger(__name__)

#: Serialises ingests against each other. Railway skips an overlapping cron run rather
#: than stacking it, so this is belt and braces — it also covers a hand-run ingest (the
#: Pi, a local backfill) landing on top of the scheduled one.
_ADVISORY_LOCK_KEY = 20260811

#: Versions retained: the current one and the one it superseded. Rolling back a bad
#: ingest is then an ``UPDATE … SET is_current`` rather than another download.
_VERSIONS_KEPT = 2

#: How stale the projection may get before the control panel calls it out. Bungie ships
#: a manifest every week or two, so a fortnight of silence means the ingest is broken
#: rather than that nothing has changed.
STALE_AFTER = dt.timedelta(days=8)


async def alert(message: str) -> None:
    """Best-effort failure notice to the alerts webhook, if one is configured.

    A plain Discord webhook URL rather than a bot token: this service has no reason to
    hold credentials that can do anything but say "the ingest failed", and a cron job
    that cannot post an alert must still exit with its real status, so every failure
    here is swallowed after logging.
    """
    url = cfg.manifest_ingest_alert_webhook
    if not url:
        return
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session,
            session.post(url, json={"content": message[:1900]}) as response,
        ):
            response.raise_for_status()
    except Exception:
        logger.warning("Could not deliver the ingest alert", exc_info=True)


async def _lock(session: t.Any) -> None:
    """Take the transaction-scoped advisory lock (Postgres only; released on commit)."""
    if schemas.db_engine.url.get_backend_name() == "sqlite":
        return
    # Transaction-scoped, so there is no unlock path to get wrong: the commit or
    # rollback that ends the ingest releases it either way.
    await session.execute(select(func.pg_advisory_xact_lock(_ADVISORY_LOCK_KEY)))


async def _delete_versions(session: t.Any, version_ids: t.Collection[int]) -> None:
    """Remove versions and every projection row that belongs to them.

    The projection rows are deleted **explicitly** rather than left to the FK's
    ``ON DELETE CASCADE``. The cascade is real on Postgres and is kept as the backstop
    for a hand-run ``DELETE FROM manifest_version``, but SQLite does not enforce foreign
    keys unless the connection asks it to — so leaning on it alone would mean retention
    silently orphaning rows on the backend the test suite runs, which is exactly where
    it would go unnoticed. Being explicit costs ten statements and behaves identically
    on both.
    """
    if not version_ids:
        return
    ids = sorted(version_ids)
    for model in schemas.MANIFEST_PROJECTION_TABLES:
        await session.execute(delete(model).where(model.version_id.in_(ids)))
    await session.execute(
        delete(schemas.ManifestVersion).where(schemas.ManifestVersion.id.in_(ids))
    )


async def _write_projection(session: t.Any, sqlite_path: str, version: str) -> int:
    """Insert a new version and its whole projection; return the new version id.

    Called inside the caller's transaction — nothing here commits.
    """
    # A version string can reappear: retention keeps the superseded row, so re-ingesting
    # after a rollback would collide with its UNIQUE constraint. Drop the stale copy and
    # build it fresh rather than trying to reconcile what is already there.
    stale = (
        await session.execute(
            select(schemas.ManifestVersion.id).where(
                schemas.ManifestVersion.version == version
            )
        )
    ).scalars()
    await _delete_versions(session, [int(i) for i in stale])

    # Inserted not-current: the projection rows are written under this id first, and
    # only the flip at the end of the transaction makes any of it visible to a reader.
    version_id = int(
        (
            await session.execute(
                insert(schemas.ManifestVersion)
                .values(
                    version=version,
                    ingested_at=dt.datetime.now(dt.UTC).replace(tzinfo=None),
                    is_current=False,
                )
                .returning(schemas.ManifestVersion.id)
            )
        ).scalar_one()
    )

    total = 0
    for model, rows in project.iter_batches(sqlite_path, version_id):
        await session.execute(insert(model), rows)
        total += len(rows)
    logger.info("Wrote %d projection rows for version %s", total, version)
    return version_id


async def _flip_and_prune(session: t.Any, version_id: int) -> None:
    """Make ``version_id`` current and drop everything but the two newest versions.

    The clear-then-set order is load-bearing: ``ix_manifest_version_current`` is a
    partial *unique* index, so setting the new row current before clearing the old one
    would fail on the second row rather than swap them.
    """
    superseded = await session.execute(
        select(schemas.ManifestVersion.id).where(schemas.ManifestVersion.is_current)
    )
    keep = {version_id, *(int(i) for i in superseded.scalars())}

    await session.execute(
        update(schemas.ManifestVersion)
        .where(schemas.ManifestVersion.is_current)
        .values(is_current=False)
    )
    await session.execute(
        update(schemas.ManifestVersion)
        .where(schemas.ManifestVersion.id == version_id)
        .values(is_current=True)
    )

    # `keep` is at most _VERSIONS_KEPT already (there is only ever one current row to
    # supersede); the delete is what enforces it against any older rows an interrupted
    # run left behind.
    assert len(keep) <= _VERSIONS_KEPT
    drop = (
        await session.execute(
            select(schemas.ManifestVersion.id).where(
                schemas.ManifestVersion.id.not_in(sorted(keep))
            )
        )
    ).scalars()
    await _delete_versions(session, [int(i) for i in drop])


async def ingest(api_key: str) -> bool:
    """One tick. ``True`` if a new version was ingested, ``False`` if already current.

    Raises on failure — the caller turns that into an alert and a non-zero exit, and
    the next hourly tick simply tries again.
    """
    version, path_fragment = await bungie.current_version(api_key)

    current = await schemas.ManifestVersion.current()
    if current is not None and current.version == version:
        logger.info("Manifest %s is already current; nothing to do", version)
        return False

    logger.info(
        "Manifest moved to %s (had %s); ingesting",
        version,
        current.version if current else "nothing",
    )

    # Ephemeral by construction: the directory goes away with this block, so there is
    # no cache between runs to go stale and no volume to pay for.
    with tempfile.TemporaryDirectory(prefix="dd-manifest-") as tmp:
        sqlite_path = await bungie.fetch_world_content(path_fragment, Path(tmp))

        async with schemas.db_session() as session:
            await _lock(session)
            # Re-checked under the lock: a concurrent run may have landed this exact
            # version while we were downloading, and re-ingesting it would be a large
            # amount of work to arrive back where we started.
            already = (
                await session.execute(
                    select(schemas.ManifestVersion.id).where(
                        schemas.ManifestVersion.version == version,
                        schemas.ManifestVersion.is_current,
                    )
                )
            ).scalar_one_or_none()
            if already is not None:
                logger.info("Another run ingested %s first; nothing to do", version)
                return False

            version_id = await _write_projection(session, str(sqlite_path), version)
            await _flip_and_prune(session, version_id)
            await session.commit()

    logger.info("Manifest %s is now current", version)
    return True


async def run() -> int:
    """Process entrypoint. Returns the exit code."""
    api_key = cfg.bungie_api_key
    if not api_key:
        # A configuration state, not a failure — the same reading the bots take of a
        # keyless environment. Exiting non-zero here would alert every hour forever.
        logger.warning("BUNGIE_API_KEY is not set; there is nothing to ingest")
        return 0

    try:
        await schemas.wait_for_db()
        await ingest(api_key)
    except Exception as exc:
        logger.exception("Manifest ingest failed")
        await alert(f"Manifest ingest failed: `{type(exc).__name__}: {exc}`")
        return 1
    finally:
        await schemas.db_engine.dispose()
    return 0


def main() -> int:
    return asyncio.run(run())
