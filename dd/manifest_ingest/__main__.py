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

"""``python -m dd.manifest_ingest`` — the Railway cron service's start command.

Deliberately **not a bot**. Nothing under ``dd/manifest_ingest/`` imports lightbulb, an
extension package, or anything that opens a gateway connection: this process exists to
check whether the manifest moved, and to rebuild the projection when it has. Keeping the
import graph that narrow is what makes the hourly fast path a few seconds rather than a
bot boot.

(``dd.common.schemas`` does pull hikari in transitively, through the ``ensure_session``
helper it shares with the bots. That is ~20 MB on an hourly job that runs for seconds —
fractions of a cent a month — and untangling it would mean splitting a module the whole
repo imports. Not worth it; do not add a *direct* hikari dependency here either way.)
"""

import sys

from .ingest import main

if __name__ == "__main__":
    sys.exit(main())
