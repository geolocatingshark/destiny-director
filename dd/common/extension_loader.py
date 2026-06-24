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

"""Loud extension loading.

``lightbulb.Client.load_extensions`` catches ``ImportError`` per module and only
logs-and-skips it quietly, so a broken extension silently disappears from the
running bot. This helper pre-imports every module in the package first: an import
failure is logged at CRITICAL with a traceback (and, since the gateway still comes
up, replayed to the alerts channel by the startup buffer) rather than vanishing.
The broken module is then skipped and the rest still load — startup is **not**
aborted.
"""

import importlib
import logging
import pkgutil
from types import ModuleType

import lightbulb as lb

logger = logging.getLogger(__name__)


async def load_extensions_strict(client: lb.Client, package: ModuleType) -> None:
    """Import and load every extension in ``package``, failing loudly.

    Mirrors ``Client.load_extensions_from_package``'s discovery (immediate children,
    skipping ``_``-prefixed names) but pre-imports each one so an import error is
    logged at CRITICAL (with traceback) instead of being silently skipped. Extensions
    that fail to import are skipped; the rest are registered via
    ``client.load_extensions`` and startup continues — a broken extension does not
    abort the bot.

    Sub-packages are included (not just top-level modules) so a large extension can be
    a package whose ``__init__`` exposes the ``loader``; only the immediate children of
    ``package`` are scanned, so a package's own sub-modules are not mistaken for
    extensions.
    """
    module_names = [
        f"{package.__name__}.{name}"
        for _finder, name, _is_pkg in pkgutil.iter_modules(package.__path__)
        if not name.startswith("_")
    ]

    loadable: list[str] = []
    for name in module_names:
        try:
            importlib.import_module(name)
        except Exception:
            logger.critical("Failed to import extension %r", name, exc_info=True)
        else:
            loadable.append(name)

    await client.load_extensions(*loadable)
