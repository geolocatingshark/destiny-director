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

# Content checks for the anchor bot's detailed /help pages.

from dd.anchor.help_details import HELP_DETAILS, POST_JSON_DETAIL
from dd.common.help import render_detail_sections


def test_post_json_is_registered_detail() -> None:
    assert POST_JSON_DETAIL in HELP_DETAILS
    # The key must match PostJson's registered context-menu name exactly.
    assert POST_JSON_DETAIL.command == "Post as JSON"


def test_post_json_walkthrough_renders() -> None:
    joined = "\n".join(render_detail_sections(POST_JSON_DETAIL))
    assert "Post as JSON" in joined
    assert "1." in joined  # numbered steps present
    assert "Apps" in joined  # the right-click invocation hint


def test_detail_keys_are_unique() -> None:
    keys = [d.command.casefold() for d in HELP_DETAILS]
    assert len(keys) == len(set(keys))
