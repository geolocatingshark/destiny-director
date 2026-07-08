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

from __future__ import annotations

import asyncio as aio
import datetime as dt
import logging
import os
import sys
import typing as t
from typing import Self

import regex as re
from atlas_provider_sqlalchemy.ddl import print_ddl
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Mapped, declarative_base, mapped_column, validates
from sqlalchemy.sql import insert, select, text, update
from sqlalchemy.sql.expression import and_, delete, desc
from sqlalchemy.sql.functions import coalesce, func
from sqlalchemy.sql.schema import CheckConstraint, Column, UniqueConstraint
from sqlalchemy.sql.sqltypes import (
    JSON,
    VARCHAR,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Integer,
    String,
    Text,
)

from dd.common import cfg
from dd.common.utils import FriendlyValueError, check_number_of_layers, ensure_session

Base = declarative_base()


class _SessionmakerProxy:
    """Stable indirection over an ``async_sessionmaker``.

    The object identity is captured by ``@ensure_session`` at import time and by
    ``from schemas import db_session`` in the beacon extensions. Calling the proxy
    forwards to the *current* inner sessionmaker, so rebinding the inner maker (via
    ``configure_test_db``) transparently repoints every existing call site at a new
    engine without re-importing or re-decorating anything."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    def __call__(self) -> AsyncSession:
        return self._sessionmaker()

    def rebind(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker


def _build_engine() -> AsyncEngine:
    return create_async_engine(
        cfg.db_url_async, connect_args=cfg.db_connect_args, **cfg.db_engine_args
    )


db_engine: AsyncEngine = _build_engine()
db_session = _SessionmakerProxy(async_sessionmaker(db_engine, **cfg.db_session_kwargs))


def configure_test_db(engine: AsyncEngine) -> None:
    """Repoint the module-global engine and the ``db_session`` proxy at ``engine``.

    Used by the test harness to swap the production MySQL engine for a throwaway
    SQLite engine. Affects every ``@ensure_session`` method, every direct
    ``db_session()`` call site, and every module-level ``db_engine`` reader at once."""
    global db_engine
    db_engine = engine
    db_session.rebind(async_sessionmaker(engine, **cfg.db_session_kwargs))


def reset_db() -> None:
    """Restore the production engine/sessionmaker (call on test teardown)."""
    global db_engine
    db_engine = _build_engine()
    db_session.rebind(async_sessionmaker(db_engine, **cfg.db_session_kwargs))


# Sentinel used as default for session parameters in @ensure_session-decorated methods.
# The decorator always supplies a real AsyncSession before the function body runs.
_UNSET: AsyncSession = t.cast(AsyncSession, None)


async def wait_for_db(retry_interval: float = 10.0) -> None:
    """Block until the database accepts a connection, retrying every
    retry_interval seconds."""
    while True:
        try:
            async with db_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return
        except OperationalError:
            logging.warning("Database unavailable, retrying in %ss...", retry_interval)
            await aio.sleep(retry_interval)


# Anchor with \Z (end of string) rather than $, since $ also matches just
# before a trailing newline and would let an illegal character like "ping\n"
# slip through as a valid command name.
rgx_cmd_name_is_valid = re.compile(r"^[a-z][a-z0-9_-]{1,31}\Z")
rgx_sub_cmd_name_is_valid = re.compile(r"^[a-z]{0,1}[a-z0-9_-]{0,31}\Z")
# The difference between command and sub command name validator regexes is
# that the sub command regex needs to allow blank strings to indicate and
# match blanks for commands that aren't 3 layers deep (where the last and)
# potentially the second last layer will be blank


class MirroredChannel(Base):
    """Mirror channels model

    with a cache for the list of all legacy source channgel ids only.
    Note, the src_ids cache will not remove elements from the cache even
    if the last mirror from it has been disabled"""

    __tablename__ = "mirrored_channel"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (UniqueConstraint("src_id", "dest_id", name="_mir_ids_uc"),)
    src_id = Column("src_id", BigInteger, primary_key=True)
    dest_id = Column("dest_id", BigInteger, primary_key=True)
    dest_server_id = Column("dest_server_id", BigInteger)
    legacy = Column("legacy", Boolean)
    enabled = Column("enabled", Boolean, default=True)
    role_mention_id = Column("role_mention_id", BigInteger, default=None)
    legacy_error_rate = Column("legacy_error_rate", Integer, default=0)
    legacy_disable_for_failure_on_date = Column(
        "legacy_disable_for_failure_on_date", DateTime, default=None
    )
    _legacy_srcs_cache = set()

    def __init__(
        self,
        src_id: int,
        dest_id: int,
        dest_server_id: int,
        legacy: bool,
        enabled: bool,
        role_mention_id: int | None,
    ):
        super().__init__()
        self.src_id = int(src_id)
        self.dest_id = int(dest_id)
        self.dest_server_id = dest_server_id and int(dest_server_id)
        self.legacy = bool(legacy)
        self.enabled = bool(enabled)
        self.role_mention_id = role_mention_id and int(role_mention_id)

    @classmethod
    @ensure_session(db_session)
    async def add_mirror(
        cls,
        src_id: int,
        dest_id: int,
        dest_server_id: int,
        legacy: bool,
        enabled: bool = True,
        role_mention_id: int | None = 0,
        session: AsyncSession = _UNSET,
    ) -> None:
        src_id = int(src_id)
        dest_id = int(dest_id)
        await session.merge(
            cls(
                src_id,
                dest_id,
                dest_server_id,
                legacy,
                enabled=enabled,
                role_mention_id=role_mention_id,
            )
        )

        if legacy and src_id not in cls._legacy_srcs_cache:
            cls._legacy_srcs_cache.add(src_id)

    @classmethod
    @ensure_session(db_session)
    async def fetch_dests(
        cls,
        src_id: int,
        legacy: bool | None = True,
        enabled: bool | None = True,
        session: AsyncSession = _UNSET,
    ) -> list[int]:
        """Fetch all dests for a given src_id

        src_id -> The source channel ID
        legacy -> True: Fetch legacy only, False: Fetch non-legacy only, None: Fetch all
        enabled -> True: Fetch enabled only, False: Fetch disabled only, None: Fetch all
        """
        src_id = int(src_id)
        dests = await session.execute(
            select(cls.dest_id)
            .where(
                and_(
                    cls.src_id == src_id,
                    (cls.legacy == legacy) if legacy is not None else True,
                    (cls.enabled == enabled) if enabled is not None else True,
                )
            )
            .join(
                ServerStatistics,
                cls.dest_server_id == ServerStatistics.id,
                isouter=True,
            )
            .order_by(
                desc(coalesce(ServerStatistics.population, 10**12)),
            )
        )

        dests = dests if dests else []
        dests = [dest[0] for dest in dests]
        return dests

    @classmethod
    @ensure_session(db_session)
    async def fetch_mirror_and_role_mention_id(
        cls,
        src_id: int,
        session: AsyncSession = _UNSET,
    ) -> dict[int, int]:
        """Fetch all dests with corresponding mirror_ping_role id for a given src_id

        src_id -> The source channel ID
        """
        src_id = int(src_id)
        mention_ids = await session.execute(
            select(cls.dest_id, cls.role_mention_id).where(and_(cls.src_id == src_id))
        )

        mention_ids = mention_ids if mention_ids else []
        mention_ids = {dest[0]: dest[1] for dest in mention_ids}
        return mention_ids

    @classmethod
    @ensure_session(db_session)
    async def fetch_srcs(
        cls,
        dest_id: int,
        legacy: bool | None = True,
        enabled: bool | None = True,
        session: AsyncSession = _UNSET,
    ) -> list[int]:
        dest_id = int(dest_id)
        srcs = (
            await session.execute(
                select(cls.src_id).where(
                    and_(
                        cls.dest_id == dest_id,
                        (cls.legacy == legacy) if legacy is not None else True,
                        (cls.enabled == enabled) if enabled is not None else True,
                    )
                )
            )
        ).fetchall()
        srcs = srcs if srcs else []
        srcs = [src[0] for src in srcs]
        return srcs

    @classmethod
    @ensure_session(db_session)
    async def get_or_fetch_all_srcs(
        cls,
        legacy: bool | None = True,
        session: AsyncSession = _UNSET,
    ) -> set[int]:
        """Fetch all srcs

        WARNING: This is function has a silent failure mode where it will return
        src_ids that may have been deleted with the removal of the last mirror
        with this src. This is intentional to avoid the overhead of clearing
        and refetching the cache on every mirror removal.

        If you need to ensure that the returned src_ids are valid, use
        fetch_all_srcs instead"""
        if legacy and cls._legacy_srcs_cache:
            return cls._legacy_srcs_cache
        else:
            srcs = await cls.fetch_all_srcs(legacy=legacy, session=session)
            if legacy:
                cls._legacy_srcs_cache = set(srcs)
            return srcs

    @classmethod
    @ensure_session(db_session)
    async def fetch_all_srcs(
        cls,
        legacy: bool | None = True,
        session: AsyncSession = _UNSET,
    ) -> set[int]:
        srcs = (
            await session.execute(select(cls.src_id).where(cls.legacy == legacy))
        ).fetchall()
        srcs = srcs if srcs else []
        srcs = [src[0] for src in srcs]
        return set(srcs)

    @classmethod
    @ensure_session(db_session)
    async def count_dests(
        cls,
        src_id: int,
        legacy_only: bool | None = True,
        session: AsyncSession = _UNSET,
    ) -> int:
        src_id = int(src_id)
        dests_count = (
            await session.execute(
                select(func.count())
                .select_from(cls)
                .where(
                    and_(
                        cls.enabled,
                        cls.src_id == src_id,
                        (cls.legacy == legacy_only)
                        if legacy_only is not None
                        else True,
                    )
                )
            )
        ).scalar_one()

        return dests_count

    @classmethod
    @ensure_session(db_session)
    async def count_total_dests(
        cls,
        legacy_only: bool | None = True,
        session: AsyncSession = _UNSET,
    ) -> int:
        dests_count = (
            await session.execute(
                select(func.count())
                .select_from(cls)
                .where(
                    (cls.legacy == legacy_only) if legacy_only is not None else True,
                )
            )
        ).scalar_one()

        return dests_count

    @classmethod
    @ensure_session(db_session)
    async def set_legacy(
        cls,
        src_id: int,
        dest_id: int,
        legacy: bool = True,
        session: AsyncSession = _UNSET,
    ) -> None:
        src_id = int(src_id)
        dest_id = int(dest_id)
        await session.execute(
            update(cls)
            .where(and_(cls.src_id == src_id, cls.dest_id == dest_id))
            .values(legacy=legacy)
        )
        if legacy:
            if src_id not in cls._legacy_srcs_cache:
                cls._legacy_srcs_cache.add(src_id)
        else:
            if src_id in cls._legacy_srcs_cache:
                cls._legacy_srcs_cache.remove(src_id)

    @classmethod
    @ensure_session(db_session)
    async def remove_mirror(
        cls, src_id: int, dest_id: int, session: AsyncSession = _UNSET
    ) -> None:
        src_id = int(src_id)
        dest_id = int(dest_id)
        await session.execute(
            update(cls)
            .where(and_(cls.src_id == src_id, cls.dest_id == dest_id, cls.enabled))
            .values(enabled=False)
        )

        # Note: We deliberately don't remove the src_id from the _all_srcs_cache
        # since we don't know if there are other mirrors with the same src_id
        # and clearing and refetching would be needlessly expensive
        # Since mirrors are mostly designed as a one to many repeater of messages
        # it is also unlikely that the last mirror with a given src_id will be
        # removed.

    @classmethod
    @ensure_session(db_session)
    async def remove_all_mirrors(
        cls, dest_id: int, session: AsyncSession = _UNSET
    ) -> None:
        dest_id = int(dest_id)
        await session.execute(
            update(cls)
            .where(and_(cls.dest_id == dest_id, cls.enabled))
            .values(enabled=False)
        )

        # Note: We deliberately don't remove the src_ids from the _all_srcs_cache
        # since we don't know if there are other mirrors with the same src_id
        # and clearing and refetching would be needlessly expensive
        # Since mirrors are mostly designed as a one to many repeater of messages
        # it is also unlikely that the last mirror with a given src_id will be
        # removed.

    @classmethod
    @ensure_session(db_session)
    async def log_legacy_mirror_success_in_batch(
        cls, src_id: int, dest_ids: list[int], session: AsyncSession = _UNSET
    ) -> None:
        """Log the successful use of a batch of mirror pairs

        In case of a successful mirror, the error rate is set to 0
        """
        src_id = int(src_id)
        dest_ids = [int(dest_id) for dest_id in dest_ids]
        await session.execute(
            update(cls)
            .where(
                and_(
                    cls.src_id == src_id,
                    cls.dest_id.in_(dest_ids),
                    cls.enabled,
                    cls.legacy,
                )
            )
            .values(legacy_error_rate=0)
        )

    @classmethod
    @ensure_session(db_session)
    async def log_legacy_mirror_failure_in_batch(
        cls, src_id: int, dest_ids: list[int], session: AsyncSession = _UNSET
    ) -> None:
        """Log the failure of a batch of mirror pairs

        In case of a failure, the error rate is increased by 1
        """
        src_id = int(src_id)
        dest_ids = [int(dest_id) for dest_id in dest_ids]
        await session.execute(
            update(cls)
            .where(
                and_(
                    cls.src_id == src_id,
                    cls.dest_id.in_(dest_ids),
                    cls.enabled,
                    cls.legacy,
                )
            )
            .values(legacy_error_rate=cls.legacy_error_rate + 1)
        )

    @classmethod
    @ensure_session(db_session)
    async def get_legacy_failing_mirrors(
        cls,
        threshold: int = 3,
        session: AsyncSession = _UNSET,
    ) -> list[tuple[int, int]]:
        """Return mirrors that have failed too many times

        Mirrors that have failed more than `threshold` times are disabled
        """
        disabled_mirrors = await session.execute(
            select(cls.src_id, cls.dest_id).where(
                and_(
                    cls.enabled,
                    cls.legacy,
                    cls.legacy_error_rate >= threshold,
                )
            )
        )
        disabled_mirrors = disabled_mirrors if disabled_mirrors else []
        disabled_mirrors = disabled_mirrors.fetchall()

        return disabled_mirrors

    @classmethod
    @ensure_session(db_session)
    async def disable_legacy_failing_mirrors(
        cls,
        threshold: int = 3,
        session: AsyncSession = _UNSET,
    ) -> list[tuple[int, int]]:
        """Disable mirrors that have failed too many times

        Mirrors that have failed more than `threshold` times are disabled
        Returns the disabled mirrors
        """
        mirrors_to_disable = await cls.get_legacy_failing_mirrors(
            threshold=threshold, session=session
        )
        await session.execute(
            update(cls)
            # Match the failing rows by the SAME predicate the SELECT used. Rebuilding
            # ``src_id IN (...) AND dest_id IN (...)`` from the pairs matches the
            # Cartesian product of the two id sets, so it would also disable innocent
            # ``(src, dest)`` rows (error_rate 0) that merely share a src or dest with a
            # genuinely-failing pair.
            .where(
                and_(
                    cls.enabled,
                    cls.legacy,
                    cls.legacy_error_rate >= threshold,
                )
            )
            .values(
                enabled=False,
                legacy_disable_for_failure_on_date=dt.datetime.now(tz=dt.UTC),
            )
        )

        # Note: We deliberately don't remove the src_id from the _all_srcs_cache
        # since we don't know if there are other mirrors with the same src_id
        # and clearing and refetching would be needlessly expensive
        # Since mirrors are mostly designed as a one to many repeater of messages
        # it is also unlikely that the last mirror with a given src_id will be
        # removed.

        return mirrors_to_disable

    @classmethod
    @ensure_session(db_session)
    async def get_legacy_mirrors_disabled_for_failure(
        cls, since: dt.datetime | None, session: AsyncSession = _UNSET
    ) -> list[tuple[int, int]]:
        """Return mirrors that have been disabled for failure

        Mirrors that have been disabled for failure since `since` are returned
        in the format (src_id, dest_id)
        """
        disabled_mirrors = await session.execute(
            select(cls.src_id, cls.dest_id).where(
                and_(
                    ~cls.enabled,
                    cls.legacy,
                    cls.legacy_disable_for_failure_on_date >= since,
                )
            )
        )
        disabled_mirrors = disabled_mirrors if disabled_mirrors else []
        disabled_mirrors = disabled_mirrors.fetchall()

        return disabled_mirrors

    @classmethod
    @ensure_session(db_session)
    async def undo_auto_disable_for_failure(
        cls,
        since: dt.datetime | None,
        session: AsyncSession = _UNSET,
    ) -> list[tuple[int, int]]:
        """Undo auto disable for failure of mirrors

        Mirrors that have been disabled for failure since `since` are re-enabled
        Mirrors are returned in the format (src_id, dest_id)
        """
        mirrors_to_enable = await cls.get_legacy_mirrors_disabled_for_failure(
            since=since, session=session
        )
        await session.execute(
            update(cls)
            .where(
                and_(
                    cls.src_id.in_([mirror[0] for mirror in mirrors_to_enable]),
                    cls.dest_id.in_([mirror[1] for mirror in mirrors_to_enable]),
                )
            )
            .values(
                enabled=True,
                legacy_error_rate=0,
            )
        )

        # Add reenabled mirrors to the cache
        cls._legacy_srcs_cache.update(set([src_id for src_id, _ in mirrors_to_enable]))

        return mirrors_to_enable


class MirroredMessage(Base):
    __tablename__ = "mirrored_message"
    __mapper_args__ = {"eager_defaults": True}
    dest_msg = Column("dest_msg", BigInteger, primary_key=True)
    dest_channel = Column("dest_ch", BigInteger)
    source_msg = Column("source_msg", BigInteger)
    source_channel = Column("src_ch", BigInteger)
    creation_datetime = Column(
        "creation_datetime", DateTime, default=lambda: dt.datetime.now(tz=dt.UTC)
    )

    def __init__(
        self,
        dest_msg: int,
        dest_channel: int,
        source_msg: int,
        source_channel: int,
        creation_datetime: dt.datetime | None = None,
    ):
        super().__init__()
        self.dest_msg = int(dest_msg)
        self.dest_channel = int(dest_channel)
        self.source_msg = int(source_msg)
        self.source_channel = int(source_channel)
        self.creation_datetime = creation_datetime or dt.datetime.now(tz=dt.UTC)

    @classmethod
    @ensure_session(db_session)
    async def add_msg(
        cls,
        dest_msg: int,
        dest_channel: int,
        source_msg: int,
        source_channel: int,
        session: AsyncSession = _UNSET,
    ) -> None:
        """Create a session, begin it and add a message pair"""
        dest_msg = int(dest_msg)
        dest_channel = int(dest_channel)
        source_msg = int(source_msg)
        source_channel = int(source_channel)

        await session.execute(
            insert(cls).values(
                dest_msg=dest_msg,
                dest_channel=dest_channel,
                source_msg=source_msg,
                source_channel=source_channel,
            )
        )

    @classmethod
    @ensure_session(db_session)
    async def add_msgs_in_batch(
        cls,
        dest_msgs: list[int],
        dest_channels: list[int],
        source_msg: int,
        source_channel: int,
        session: AsyncSession = _UNSET,
    ) -> None:
        """Create a session, begin it and add a message pair"""
        dest_msgs = [int(dest_msg) for dest_msg in dest_msgs]
        dest_channels = [int(dest_channel) for dest_channel in dest_channels]
        source_msg = int(source_msg)
        source_channel = int(source_channel)

        await session.execute(
            insert(cls).values(
                [
                    {
                        "dest_msg": dest_msg,
                        "dest_channel": dest_channel,
                        "source_msg": source_msg,
                        "source_channel": source_channel,
                    }
                    for dest_msg, dest_channel in zip(
                        dest_msgs, dest_channels, strict=True
                    )
                ]
            )
        )

    @classmethod
    @ensure_session(db_session)
    async def get_dest_msgs_and_channels(
        cls,
        source_msg: int,
        session: AsyncSession = _UNSET,
    ) -> list[tuple[int, int]]:
        """Return dest message and channel ids from source message id"""
        source_msg = int(source_msg)
        dest_msgs = (
            await session.execute(
                select(cls.dest_msg, cls.dest_channel).where(
                    cls.source_msg == source_msg
                )
            )
        ).fetchall()
        # Handle source_id not found
        dest_msgs = [] if dest_msgs is None else dest_msgs
        return dest_msgs

    @classmethod
    @ensure_session(db_session)
    async def prune(
        cls,
        age: None | dt.timedelta = dt.timedelta(days=21),
        session: AsyncSession = _UNSET,
    ) -> None:
        """Delete entries older than <age>"""
        await session.execute(
            delete(cls).where(dt.datetime.now(tz=dt.UTC) - age > cls.creation_datetime)
        )


class ServerStatistics(Base):
    __tablename__ = "server_statistics"
    __mapper_args__ = {"eager_defaults": True}
    # Server id
    id = Column("id", BigInteger, primary_key=True)
    population = Column("population", BigInteger)

    # Population is set high by default since its better to prioritize
    # new servers if we don't yet know their population
    def __init__(self, id: int, population: int = 10**12):
        self.id = int(id)
        self.population = int(population)

    @classmethod
    @ensure_session(db_session)
    async def add_server(
        cls,
        id: int,
        population: int = 10**12,
        session: AsyncSession = _UNSET,
    ) -> None:
        id = int(id)
        await session.merge(cls(id, population))

    @classmethod
    @ensure_session(db_session)
    async def add_servers_in_batch(
        cls,
        ids: list[int],
        populations: list[int],
        session: AsyncSession = _UNSET,
    ) -> None:
        ids = [int(id) for id in ids]
        populations = [int(population) for population in populations]
        if len(ids) != len(populations):
            raise ValueError("ids and populations must be of the same length")

        if not (ids and populations):
            return

        await session.execute(
            insert(cls),
            [
                {"id": id, "population": population}
                for id, population in zip(ids, populations, strict=True)
            ],
        )

    @classmethod
    @ensure_session(db_session)
    async def fetch_server_ids(
        cls,
        session: AsyncSession = _UNSET,
    ) -> list[int]:
        ids = await session.execute(select(cls.id))
        ids = ids if ids else []
        ids = [id[0] for id in ids]
        return ids

    @classmethod
    @ensure_session(db_session)
    async def fetch_server_populations(
        cls, session: AsyncSession = _UNSET
    ) -> list[tuple[int, int]]:
        """Returns tuples of server id to population"""
        populations = (await session.execute(select(cls.id, cls.population))).fetchall()
        populations = populations if populations else []
        return populations

    @classmethod
    @ensure_session(db_session)
    async def update_population(
        cls,
        id: int,
        population: int,
        session: AsyncSession = _UNSET,
    ) -> None:
        id = int(id)
        await session.execute(
            update(cls).where(cls.id == id).values(population=population)
        )

    @classmethod
    @ensure_session(db_session)
    async def update_population_in_batch(
        cls,
        ids: list[int],
        populations: list[int],
        session: AsyncSession = _UNSET,
    ) -> None:
        ids = [int(id) for id in ids]
        populations = [int(population) for population in populations]
        await session.execute(
            update(cls),
            [
                {"id": id, "population": population}
                for id, population in zip(ids, populations, strict=True)
            ],
        )


class CommandUsage(Base):
    """Per-command, per-day invocation counts for user-facing slash commands.

    Daily buckets keep growth bounded (commands × days) while supporting both
    all-time totals and time-windowed queries. Writes use a MySQL upsert with an
    atomic ``count = count + 1`` so concurrent increments are race-free with no
    in-memory buffering.
    """

    __tablename__ = "command_usage"
    __mapper_args__ = {"eager_defaults": True}

    command_name = Column("command_name", String(length=128), primary_key=True)
    date = Column("date", Date, primary_key=True)  # daily bucket, UTC
    count = Column("count", BigInteger, nullable=False, default=0)

    @classmethod
    @ensure_session(db_session)
    async def increment(
        cls, command_name: str, *, session: AsyncSession = _UNSET
    ) -> None:
        today = dt.datetime.now(tz=dt.UTC).date()
        stmt = mysql_insert(cls).values(command_name=command_name, date=today, count=1)
        await session.execute(stmt.on_duplicate_key_update(count=cls.count + 1))

    @classmethod
    @ensure_session(db_session)
    async def fetch_totals(
        cls, *, since: dt.date | None = None, session: AsyncSession = _UNSET
    ) -> list[tuple[str, int]]:
        q = select(cls.command_name, func.sum(cls.count).label("total"))
        if since is not None:
            q = q.where(cls.date >= since)
        q = q.group_by(cls.command_name).order_by(desc("total"))
        return [(name, int(total)) for name, total in (await session.execute(q)).all()]

    @classmethod
    @ensure_session(db_session)
    async def fetch_daily(
        cls, *, since: dt.date, session: AsyncSession = _UNSET
    ) -> list[tuple[str, dt.date, int]]:
        """Per-command daily counts on/after ``since`` as ``(name, date, count)`` rows.

        Ordered by name then date. The presentation layer derives both windowed totals
        (for trend deltas) and per-day series (for sparklines) from these rows, so the
        windowing math stays in pure, testable Python rather than SQL.
        """
        q = (
            select(cls.command_name, cls.date, cls.count)
            .where(cls.date >= since)
            .order_by(cls.command_name, cls.date)
        )
        return [(n, d, int(c)) for n, d, c in (await session.execute(q)).all()]


class UserCommand(Base):
    __tablename__ = "user_command"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint("l1_name", "l2_name", "l3_name", name="_ln_name_uc"),
        # Make sure if l3_name is empty then response_type is 0
        # ie either l3_name can be empty or response_type can be non 0
        CheckConstraint("l3_name = '' OR response_type <> 0"),
        # Make sure if l2_name is empty, the so is l3
        CheckConstraint("(l2_name = '' AND l3_name = '') OR (l2_name <> '')"),
    )
    id: Mapped[int] = mapped_column("id", Integer, primary_key=True)
    l1_name: Mapped[str] = mapped_column("l1_name", String(length=32), nullable=True)
    l2_name: Mapped[str] = mapped_column("l2_name", String(length=32), nullable=True)
    l3_name: Mapped[str] = mapped_column("l3_name", String(length=32), nullable=True)
    # command_name can include spaces, must match rgx_command_name_is_valid
    description: Mapped[str] = mapped_column(
        "description", String(length=256), nullable=True
    )
    response_type: Mapped[int] = mapped_column("response_type", Integer, nullable=True)
    # response_types are as follows:
    # 0: No response, ie this is a command group
    # 1: Plain text, respondes directly with response_data column text
    # 2: Message copy, fetches a message and responds with a copy of its content,
    #    embeds, components and attachments. response_data must be a Discord message
    #    link (…/channels/<guild_id>/<channel_id>/<message_id>); the channel_id and
    #    message_id are taken from the last two path segments. Note: the bot must be
    #    able to fetch the message, so check it is accessible before adding to db
    # 3: Embed, responds by parsing response data, parsing the same as
    #    json, and passing it to hikari.Embed(...). This embed is sent
    #    as a response
    response_data: Mapped[str] = mapped_column(Text, nullable=True)

    def __init__(
        self,
        l1_name: str,
        l2_name: str = "",
        l3_name: str = "",
        *,
        description: str,
        response_type: int,
        response_data: str = "",
    ):
        self.l1_name = str(l1_name)
        self.l2_name = str(l2_name)
        self.l3_name = str(l3_name)
        self.description = str(description)
        self.response_type = int(response_type)
        self.response_data = str(response_data)

    def __repr__(self) -> str:
        return " -> ".join(
            ln_name for ln_name in [self.l1_name, self.l2_name, self.l3_name] if ln_name
        )

    @validates("l1_name", "l2_name", "l3_name")
    def command_name_validator(self, key: str, value: str) -> str:
        """Restrict to valid discord command names"""
        value = str(value)
        if (
            key == "l1_name"
            and rgx_cmd_name_is_valid.match(value)
            or key in ["l2_name", "l3_name"]
            and rgx_sub_cmd_name_is_valid.match(value)
        ):
            return value
        else:
            raise FriendlyValueError(
                "Command names must start with a letter, be all lowercase, and only "
                + "contain letter, numbers, dashes (-) and underscores (_) and must "
                + "not be longer than 32 characters. Spaces cannot be used."
            )

    @classmethod
    @ensure_session(db_session)
    async def fetch_commands(cls, session: AsyncSession = _UNSET) -> list[UserCommand]:
        commands = (
            await session.execute(select(cls).where(cls.response_type != 0))
        ).fetchall()
        commands = commands if commands else []
        commands = [command[0] for command in commands]
        return commands

    @classmethod
    @ensure_session(db_session)
    async def fetch_command_groups(
        cls, session: AsyncSession = _UNSET
    ) -> list[UserCommand]:
        commands = (
            await session.execute(
                select(cls)
                .where(cls.response_type == 0)
                .order_by(cls.l1_name, cls.l2_name, cls.l3_name)
            )
        ).fetchall()
        commands = commands if commands else []
        commands = [command[0] for command in commands]
        return commands

    @classmethod
    @ensure_session(db_session)
    async def fetch_command(
        cls, *ln_names: str, session: AsyncSession = _UNSET
    ) -> UserCommand | None:
        check_number_of_layers(ln_names)

        # Pad ln_names with "" up to len 3
        ln_names = list(ln_names)
        ln_names.extend([""] * (3 - len(ln_names)))

        return (
            await session.execute(
                select(cls).where(
                    and_(
                        cls.l1_name == ln_names[0],
                        cls.l2_name == ln_names[1],
                        cls.l3_name == ln_names[2],
                        cls.response_type != 0,
                    )
                )
            )
        ).scalar()

    @classmethod
    @ensure_session(db_session)
    async def fetch_command_group(
        cls, *ln_names: str, session: AsyncSession = _UNSET
    ) -> UserCommand | None:
        if len(ln_names) >= 3:
            raise FriendlyValueError(
                "Discord does not support slash command groups more than "
                + "2 layers deep"
            )
        elif len(ln_names) == 0:
            raise ValueError("Too few ln_names provided, need at least 1")

        # Pad ln_names with "" up to len 3
        ln_names = list(ln_names)
        ln_names.extend([""] * (2 - len(ln_names)))

        return (
            await session.execute(
                select(cls).where(
                    and_(
                        cls.l1_name == ln_names[0],
                        cls.l2_name == ln_names[1],
                        cls.response_type == 0,
                    )
                )
            )
        ).scalar()

    @classmethod
    @ensure_session(db_session)
    async def _autocomplete(
        cls,
        l1_name: str = "",
        l2_name: str = "",
        l3_name: str = "",
        session: AsyncSession = _UNSET,
    ) -> list[UserCommand]:
        completions = (
            await session.execute(
                select(cls).where(
                    (cls.l1_name + cls.l2_name + cls.l3_name).startswith(
                        l1_name + l2_name + l3_name
                    )
                )
            )
        ).fetchall()
        completions = completions if completions else []
        completions = [completion[0] for completion in completions]
        return completions

    @classmethod
    @ensure_session(db_session)
    async def add_command(
        cls,
        *ln_names: str,  # Layer n names
        description: str,
        response_type: int,
        response_data: str,
        session: AsyncSession = _UNSET,
    ) -> UserCommand:
        check_number_of_layers(ln_names)
        await cls.check_parent_command_groups_exist(*ln_names, session=session)

        # Check if there is an existing command with the same name
        existing_command = await cls.fetch_command(*ln_names, session=session)
        if existing_command:
            raise FriendlyValueError(
                f"Command {' -> '.join(filter(lambda n: n != '', ln_names))} "
                "already exists"
            )

        self = cls(
            *ln_names,
            description=description,
            response_type=response_type,
            response_data=response_data,
        )
        session.add(self)
        return self

    @classmethod
    @ensure_session(db_session)
    async def add_command_group(
        cls, *ln_names, description, session: AsyncSession = _UNSET
    ) -> UserCommand:
        return await cls.add_command(
            *ln_names,
            description=description,
            response_type=0,  # Response type 0 for command groups
            session=session,
            response_data=None,
        )

    @classmethod
    @ensure_session(db_session)
    async def check_parent_command_groups_exist(
        cls,
        l1_name: str,
        l2_name: str = "",
        l3_name: str = "",
        session: AsyncSession = _UNSET,
    ) -> bool:
        """Check if the parent command groups exist

        Note, this is different from the command existing (response_type must be 0)

        raises FriendlyValueError if command groups specified do not exist"""

        if l2_name:
            # Only check l1_name if l3_name command is provided
            l1_exists = (
                await session.execute(
                    select(cls.id).where(
                        # Check whether l1_name exists with a 0 response type
                        # since 0 response types signify a command group
                        and_(cls.l1_name == l1_name, cls.response_type == 0)
                    )
                )
            ).scalar()
            # scalar only returns false (None) when no rows are found

            if not l1_exists:
                raise FriendlyValueError(
                    f"{l1_name} is not an existing command group",
                )

        if l3_name:
            # Only check if l2_name exists if l3_name command is provided
            l2_exists = (
                await session.execute(
                    select(cls.id).where(
                        and_(
                            # Check whether l1_name -> l2_name exists with a 0 response
                            # type since 0 response types signify a command group
                            cls.l1_name == l1_name,
                            cls.l2_name == l2_name,
                            cls.response_type == 0,
                        )
                    )
                )
            ).scalar()
            # scalar only returns false (None) when no rows are found

            if not l2_exists:
                raise FriendlyValueError(
                    f"{l1_name} -> {l2_name} is not an existing command group",
                )

        # Return true if command groups exist
        return True

    @classmethod
    @ensure_session(db_session)
    async def fetch_subcommands(
        cls, l1_name, l2_name: str = "", session: AsyncSession = _UNSET
    ) -> list[UserCommand]:
        return (
            await session.execute(
                select(cls).where(
                    and_(
                        cls.l1_name == l1_name,
                        # The below is to handle subcommands of command groups
                        # at the top layer where l2_name will not be specified
                        # when trying to fetch subcommands
                        (cls.l2_name == l2_name) if l2_name else True,
                        cls.response_type != 0,
                    )
                )
            )
        ).fetchall()

    @classmethod
    @ensure_session(db_session)
    async def delete_command(
        cls,
        l1_name: str,
        l2_name: str = "",
        l3_name: str = "",
        fetch_deleted: bool = True,
        session: AsyncSession = _UNSET,
    ) -> UserCommand:
        commands_to_delete = (
            (
                await session.execute(
                    select(cls).where(
                        and_(
                            cls.l1_name == l1_name,
                            cls.l2_name == l2_name,
                            cls.l3_name == l3_name,
                            cls.response_type != 0,
                        )
                    )
                )
            ).scalar()
            if fetch_deleted  # Do not fetch if fetch_deleted is False
            else []
        )

        await session.execute(
            delete(cls).where(
                and_(
                    cls.l1_name == l1_name,
                    cls.l2_name == l2_name,
                    cls.l3_name == l3_name,
                    cls.response_type != 0,
                )
            )
        )
        return commands_to_delete

    @classmethod
    @ensure_session(db_session)
    async def delete_command_group(
        cls,
        l1_name: str,
        l2_name: str = "",
        cascade: bool = False,
        fetch_deleted: bool = True,
        session: AsyncSession = _UNSET,
    ) -> list[UserCommand]:
        subcommands = await cls.fetch_subcommands(l1_name, l2_name, session=session)
        if subcommands and not cascade:
            # Handle the case where subcommands are found and we aren't supposed
            # to cascade delete
            raise FriendlyValueError(
                f"Command group {l1_name}{(' -> ' + l2_name) if l2_name else ''} "
                + "still has subcommands"
            )
        else:
            # If cascade delete is not specified then the below will only delete the
            # command group since we already know that there are no subcommands as per
            # the above branch
            # If cascade delete is True, delete all with matching l1 & if specified l2
            # names

            deleted = (
                (
                    await session.execute(
                        select(cls).where(
                            and_(
                                cls.l1_name == l1_name,
                                (cls.l2_name == l2_name) if l2_name else True,
                            )
                        )
                    )
                ).fetchall()
                if fetch_deleted  # Do not fetch if fetch_delted is False
                else []
            )
            await session.execute(
                delete(cls).where(
                    and_(
                        cls.l1_name == l1_name,
                        (cls.l2_name == l2_name) if l2_name else True,
                    )
                )
            )
            deleted = deleted if deleted else []
            deleted = [item[0] for item in deleted]
            return deleted

    @property
    def is_command_group(self) -> bool:
        return self.response_type == 0

    @property
    def is_subcommand_or_subgroup(self) -> bool:
        return self.depth > 1

    @property
    def depth(self) -> int:
        return len(self.ln_names)

    @property
    def ln_names(self) -> list[str]:
        return [
            ln_name for ln_name in [self.l1_name, self.l2_name, self.l3_name] if ln_name
        ]


class AutoPostSettings(Base):
    __tablename__ = "auto_post_settings"
    __mapper_args__ = {"eager_defaults": True}

    name = Column("name", VARCHAR(32), primary_key=True)
    enabled = Column(
        "enabled",
        Boolean,
        default=True,
    )

    def __init__(
        self,
        name: str,
        enabled=False,
    ):
        self.name = name
        self.enabled = enabled

    @classmethod
    @ensure_session(db_session)
    async def get_enabled(
        cls, auto_post_name: str, session: AsyncSession = _UNSET
    ) -> bool | None:
        enabled = (
            await session.execute(select(cls.enabled).where(cls.name == auto_post_name))
        ).scalar()
        return enabled

    @classmethod
    @ensure_session(db_session)
    async def set_enabled(
        cls, auto_post_name: str, enabled: bool, session: AsyncSession = _UNSET
    ) -> None:
        currently_enabled = await cls.get_enabled(auto_post_name, session=session)

        if currently_enabled == enabled:
            return
        elif currently_enabled is None:
            await session.execute(
                insert(cls).values({cls.name: auto_post_name, cls.enabled: enabled})
            )
        else:
            await session.execute(
                update(cls)
                .values({cls.enabled: enabled})
                .where(cls.name == auto_post_name)
            )

    @classmethod
    async def get_eververse_enabled(cls) -> bool | None:
        return await cls.get_enabled("eververse")

    @classmethod
    async def set_eververse(cls, enabled: bool) -> None:
        return await cls.set_enabled("eververse", enabled)

    @classmethod
    async def get_lost_sector_enabled(cls) -> bool | None:
        return await cls.get_enabled("lost_sector")

    @classmethod
    async def set_lost_sector(cls, enabled: bool) -> None:
        return await cls.set_enabled("lost_sector", enabled)

    @classmethod
    async def get_lost_sector_details_enabled(cls) -> bool | None:
        return await cls.get_enabled("lost_sector_details")

    @classmethod
    async def set_lost_sector_details(cls, enabled: bool) -> None:
        return await cls.set_enabled("lost_sector_details", enabled)

    @classmethod
    async def get_xur_enabled(cls) -> bool | None:
        return await cls.get_enabled("xur")

    @classmethod
    async def set_xur(cls, enabled: bool) -> None:
        return await cls.set_enabled("xur", enabled)

    @classmethod
    async def get_xur_default_image_enabled(cls) -> bool | None:
        return await cls.get_enabled("xur_default_image")

    @classmethod
    async def set_xur_default_image_enabled(cls, enabled: bool) -> None:
        return await cls.set_enabled("xur_default_image", enabled)

    @classmethod
    async def get_ada_enabled(cls) -> bool | None:
        return await cls.get_enabled("ada")

    @classmethod
    async def set_ada(cls, enabled: bool) -> None:
        return await cls.set_enabled("ada", enabled)

    @classmethod
    async def get_portal_ops_enabled(cls) -> bool | None:
        return await cls.get_enabled("portal_ops")

    @classmethod
    async def set_portal_ops(cls, enabled: bool) -> None:
        return await cls.set_enabled("portal_ops", enabled)

    @classmethod
    async def get_weekly_reset_enabled(cls) -> bool | None:
        return await cls.get_enabled("weekly_reset")

    @classmethod
    async def set_weekly_reset(cls, enabled: bool) -> None:
        return await cls.set_enabled("weekly_reset", enabled)


class RotationData(Base):
    """Whole-document JSON store for rotation-based posts (one row per post type).

    The PK ``name`` is the post-type slug (``lost_sector``, future
    ``dares_of_eternity``…) — the same slug used by :class:`AutoPostSettings` and
    ``cfg.followables`` — so a post type is addressed identically across its
    enabled-flag, channel and data. ``data`` is the full JSON document validated by
    :mod:`dd.common.rotation_schema`; integrity lives at the app layer (schema +
    attrs construction + the tolerant loader), not in a relational shape, so new post
    types need a new *row*, never a migration.
    """

    __tablename__ = "rotation_data"
    __mapper_args__ = {"eager_defaults": True}

    name = Column("name", VARCHAR(32), primary_key=True)
    data = Column("data", JSON, nullable=False)
    updated_at = Column("updated_at", DateTime, default=None)

    @classmethod
    @ensure_session(db_session)
    async def get_data(
        cls, name: str, session: AsyncSession = _UNSET
    ) -> dict[str, t.Any] | None:
        """Return the stored JSON document for ``name``, or ``None`` if absent."""
        return (
            await session.execute(select(cls.data).where(cls.name == name))
        ).scalar()

    @classmethod
    @ensure_session(db_session)
    async def set_data(
        cls, name: str, data: dict[str, t.Any], session: AsyncSession = _UNSET
    ) -> None:
        """Upsert the whole JSON document for ``name``, stamping ``updated_at``."""
        now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
        exists = (
            await session.execute(select(cls.name).where(cls.name == name))
        ).scalar()
        if exists is None:
            await session.execute(
                insert(cls).values(
                    {cls.name: name, cls.data: data, cls.updated_at: now}
                )
            )
        else:
            await session.execute(
                update(cls)
                .values({cls.data: data, cls.updated_at: now})
                .where(cls.name == name)
            )


class BungieCredentials(Base):
    __tablename__ = "bungie_credentials"
    __mapper_args__ = {"eager_defaults": True}

    id = Column("id", Integer, primary_key=True)
    api_key = cfg.bungie_api_key
    client_id = cfg.bungie_client_id
    client_secret = cfg.bungie_client_secret
    refresh_token = Column("refresh_token", VARCHAR(1024), default=None)
    refresh_token_expires = Column("refresh_token_expires", DateTime, default=None)

    def __init__(
        self,
        id: int = 1,
        refresh_token=None,
        refresh_token_expires=None,
    ):
        self.id = id
        self.refresh_token = refresh_token
        self.refresh_token_expires = refresh_token_expires

    @classmethod
    @ensure_session(db_session)
    async def get_credentials(cls, id=1, session: AsyncSession = _UNSET) -> Self:
        return (await session.execute(select(cls).where(cls.id == id))).scalar()

    @classmethod
    @ensure_session(db_session)
    async def set_refresh_token(
        cls,
        id=1,
        refresh_token=None,
        refresh_token_expires=None,
        session: AsyncSession = _UNSET,
    ) -> None:
        refresh_token_expires = dt.datetime.now() + dt.timedelta(
            seconds=refresh_token_expires * 0.8  # 20% Factor of Safety
        )

        self = (await session.execute(select(cls.id).where(cls.id == id))).scalar()

        if self:
            await session.execute(
                update(cls)
                .where(cls.id == id)
                .values(
                    {
                        cls.refresh_token: refresh_token,
                        cls.refresh_token_expires: refresh_token_expires,
                    }
                )
            )
        else:
            await session.execute(
                insert(cls).values(
                    {
                        cls.id: id,
                        cls.refresh_token: refresh_token,
                        cls.refresh_token_expires: refresh_token_expires,
                    }
                )
            )


_LOCAL_DB_HOSTS = frozenset({None, "", "localhost", "127.0.0.1", "::1"})


def _db_is_local() -> bool:
    """Whether the *active* engine targets SQLite or a local MySQL host.

    Gates destructive schema ops (and the ``TEST_USE_MYSQL`` test path) so they can
    never wipe a shared dev/prod database. ``configure_test_db`` swaps ``db_engine``,
    so this reflects whatever backend is currently in use."""
    url = db_engine.url
    return url.get_backend_name() == "sqlite" or url.host in _LOCAL_DB_HOSTS


def _assert_schema_destroy_allowed() -> None:
    """Refuse to drop the schema of a non-local database unless explicitly forced."""
    if _db_is_local() or os.getenv("ALLOW_REMOTE_SCHEMA_DESTROY"):
        return
    raise RuntimeError(
        f"Refusing to drop the schema of a non-local database "
        f"(host={db_engine.url.host!r}). This guard stops tests / "
        "`make destroy-schemas` from wiping the dev/prod DB. Set "
        "ALLOW_REMOTE_SCHEMA_DESTROY=1 only if you truly mean it."
    )


async def destroy_all() -> None:
    _assert_schema_destroy_allowed()
    await wait_for_db()

    async with db_engine.begin() as conn:
        logging.info(f"Dropping tables: {list(Base.metadata.tables.keys())}")
        await conn.run_sync(Base.metadata.drop_all)

    await destroy_atlas_metadata()


async def destroy_atlas_metadata() -> None:
    async with db_engine.begin() as conn:
        logging.info("Dropping table: atlas_schema_revisions")
        await conn.execute(text("DROP TABLE IF EXISTS atlas_schema_revisions"))


async def create_all() -> None:
    await wait_for_db()

    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        logging.info(f"Created tables: {list(Base.metadata.tables.keys())}")


if __name__ == "__main__":
    if "--print-ddl" in sys.argv:
        print_ddl("mysql", [Base])

    if "--destroy-all" in sys.argv:
        aio.run(destroy_all())

    if "--create-all" in sys.argv:
        aio.run(create_all())
