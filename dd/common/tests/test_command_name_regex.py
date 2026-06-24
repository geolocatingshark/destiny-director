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

# Command-name validation is pure regex logic. The SQLAlchemy ``@validates`` hook
# fires on attribute assignment, so constructing a ``UserCommand`` exercises it
# without any database access.

import pytest

from dd.common.schemas import (
    UserCommand,
    rgx_cmd_name_is_valid,
    rgx_sub_cmd_name_is_valid,
)
from dd.common.utils import FriendlyValueError


@pytest.mark.parametrize("name", ["ping", "pi-zza", "a1_b", "pi"])
def test_l1_regex_accepts_valid_names(name: str):
    assert rgx_cmd_name_is_valid.match(name)


@pytest.mark.parametrize(
    "name",
    [
        "Ping",  # capital
        "p",  # too short (needs >=2 chars)
        "has space",
        "with!",
        "p" * 33,  # too long
        "",  # blank not allowed for l1
    ],
)
def test_l1_regex_rejects_invalid_names(name: str):
    assert not rgx_cmd_name_is_valid.match(name)


@pytest.mark.parametrize("name", ["", "p", "ping", "pi-zza"])
def test_sub_regex_allows_blank_and_valid_names(name: str):
    # Sub-command names may be blank (for commands that aren't 3 layers deep).
    assert rgx_sub_cmd_name_is_valid.match(name)


@pytest.mark.parametrize("name", ["Ping", "with space", "a" * 33])
def test_sub_regex_rejects_invalid_names(name: str):
    assert not rgx_sub_cmd_name_is_valid.match(name)


def test_usercommand_accepts_valid_name():
    cmd = UserCommand("ping", description="d", response_type=1, response_data="hi")
    assert cmd.l1_name == "ping"


def test_usercommand_rejects_invalid_l1_name():
    with pytest.raises(FriendlyValueError):
        UserCommand("Ping", description="d", response_type=1, response_data="hi")


def test_usercommand_allows_blank_sublayers():
    cmd = UserCommand("group", "", "", description="d", response_type=0)
    assert cmd.ln_names == ["group"]
    assert cmd.is_command_group
    assert cmd.depth == 1


def test_usercommand_three_layers_depth():
    cmd = UserCommand(
        "group", "sub", "leaf", description="d", response_type=1, response_data="x"
    )
    assert cmd.ln_names == ["group", "sub", "leaf"]
    assert cmd.depth == 3
    assert cmd.is_subcommand_or_subgroup
