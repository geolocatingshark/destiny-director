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

"""Fixtures for the ingest tests. The fixture *data* lives in ``fake_manifest``.

Split that way because both test modules want the builder and its constants, and these
directories are namespace packages with no ``__init__.py`` — importing from a sibling
module is unambiguous where importing from ``conftest`` is not (pytest gives conftest a
name of its own choosing)."""

from pathlib import Path

import pytest

from dd.manifest_ingest.tests.fake_manifest import write_manifest


@pytest.fixture
def manifest_sqlite(tmp_path: Path) -> Path:
    return write_manifest(tmp_path / "world.content")
