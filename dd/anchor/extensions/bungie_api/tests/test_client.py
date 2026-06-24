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

"""Tests for the thin Bungie HTTP client (header construction, no I/O)."""

from dd.anchor.extensions.bungie_api import client
from dd.common import schemas


def test_headers_include_api_key_and_bearer():
    headers = client._headers("tok123")
    assert headers["Authorization"] == "Bearer tok123"
    assert headers["X-API-Key"] == schemas.BungieCredentials.api_key
