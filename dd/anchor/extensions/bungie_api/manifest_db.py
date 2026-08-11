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

"""Reading the manifest projection: pinning a version, and escaping SQL patterns.

The projection is written by ``dd.manifest_ingest`` (a cron service, out of process) and
read from here. The one rule every reader follows:

    **Resolve the current version once per operation, then pin it.**

An "operation" is one post construction or one HTTP request. Pin it and a manifest flip
landing mid-operation cannot mix two seasons inside a single post; re-resolve it per
query and it can. There is no cache to invalidate and no warm-up to wait for — before
the first ingest :func:`current_version_id` is simply ``None`` and every consumer
degrades exactly as it already did for a cold index.
"""

from __future__ import annotations

import logging

from dd.common import schemas

logger = logging.getLogger(__name__)

#: The character :func:`like_escape` escapes with. Backslash is SQL's conventional
#: choice and is spelled out in each ``LIKE`` via ``ESCAPE``, because Postgres and
#: SQLite disagree about what the default is when you leave it out.
LIKE_ESCAPE = "\\"


async def current_version_id() -> int | None:
    """The current manifest version's id, or ``None`` if nothing has been ingested.

    Deliberately not memoised. It is one indexed single-row read, and a TTL cache would
    buy microseconds in exchange for a window in which two processes disagree about
    which manifest is current — the thing the version pinning exists to rule out.
    """
    return await schemas.ManifestVersion.current_id()


def like_escape(value: str) -> str:
    """Neutralise ``LIKE``'s wildcards in a user-supplied substring.

    The searches this feeds replaced Python's ``in``/``startswith``, which have no
    pattern language at all. Passing a raw query into ``LIKE`` would quietly hand the
    user two metacharacters: ``%`` (matching everything, so an autocomplete of "%"
    returns arbitrary items) and ``_`` (matching any character, which real item names
    contain). Escaping them keeps the SQL a literal substring test, which is what the
    call sites mean and what they used to do.
    """
    for char in (LIKE_ESCAPE, "%", "_"):
        value = value.replace(char, LIKE_ESCAPE + char)
    return value
