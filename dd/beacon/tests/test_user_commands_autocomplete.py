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

# Tests for ``layer_autocomplete``. lightbulb v3 autocomplete providers must respond via
# ``ctx.respond`` (the return value is discarded) with plain strings/tuples, not the
# ``lb.Choice`` objects the v2 callback returned. These guard that contract.

import typing as t

import lightbulb as lb
import pytest

from dd.beacon.extensions import user_commands as uc
from dd.common.schemas import UserCommand


async def _run(ctx: "_FakeAutocompleteContext") -> t.Any:
    # ``layer_autocomplete`` is typed for the real ``AutocompleteContext``; the fake
    # exposes the same surface, so cast at the call boundary.
    return await uc.layer_autocomplete(t.cast("lb.AutocompleteContext[str]", ctx))


class _FakeOption:
    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value


class _FakeAutocompleteContext:
    """Minimal stand-in exposing only what ``layer_autocomplete`` touches:
    ``focused``, ``get_option`` and ``respond``."""

    def __init__(self, focused_name: str, typed: str, options: dict[str, str]) -> None:
        self._focused = _FakeOption(focused_name, typed)
        self._options = options
        self.responded: t.Any = None

    @property
    def focused(self) -> _FakeOption:
        return self._focused

    def get_option(self, name: str) -> _FakeOption | None:
        if name not in self._options:
            return None
        return _FakeOption(name, self._options[name])

    async def respond(self, choices: t.Any) -> None:
        self.responded = list(choices)


def _group(*ln_names: str) -> UserCommand:
    return UserCommand(*ln_names, description="d", response_type=0)


@pytest.fixture
def _patched_autocomplete(monkeypatch: pytest.MonkeyPatch):
    """Patch ``UserCommand._autocomplete`` to return canned rows, so the test exercises
    the provider's response behaviour without touching the DB."""

    def patch(rows: list[UserCommand]) -> None:
        async def fake(*_args: t.Any, **_kwargs: t.Any) -> list[UserCommand]:
            return rows

        monkeypatch.setattr(UserCommand, "_autocomplete", fake)

    return patch


@pytest.mark.asyncio
async def test_responds_with_plain_strings_not_choices(_patched_autocomplete) -> None:
    """The provider must call ``ctx.respond`` (not return) with plain strings."""
    _patched_autocomplete([_group("alpha"), _group("beta")])
    ctx = _FakeAutocompleteContext("layer1", "", {"layer1": ""})

    result = await _run(ctx)

    assert result is None, "provider must not return choices; v3 discards the return"
    assert ctx.responded == ["alpha", "beta"]
    assert all(isinstance(c, str) for c in ctx.responded)


@pytest.mark.asyncio
async def test_filters_by_depth_and_typed_prefix(_patched_autocomplete) -> None:
    """Only rows at the focused layer's depth matching the typed prefix are offered."""
    _patched_autocomplete(
        # "append" is depth-2: its l1 matches "ap" but it must be excluded because we
        # are completing layer1 (depth 1), proving depth filtering, not just prefix.
        [_group("apple"), _group("apricot"), _group("banana"), _group("append", "deep")]
    )
    ctx = _FakeAutocompleteContext("layer1", "ap", {"layer1": "ap"})

    await _run(ctx)

    assert ctx.responded == ["apple", "apricot"]


@pytest.mark.asyncio
async def test_dedupes_and_caps_at_25(_patched_autocomplete) -> None:
    """Duplicate layer names collapse and Discord's 25-choice limit is respected."""
    rows = [_group("dup") for _ in range(3)] + [
        _group(f"name{i:02d}") for i in range(40)
    ]
    _patched_autocomplete(rows)
    ctx = _FakeAutocompleteContext("layer1", "", {"layer1": ""})

    await _run(ctx)

    assert ctx.responded is not None
    assert len(ctx.responded) == 25
    assert ctx.responded.count("dup") == 1  # de-duplicated


@pytest.mark.asyncio
async def test_guard_case_responds_empty(_patched_autocomplete) -> None:
    """A non-``layer`` focused option still closes the interaction with no rows."""
    _patched_autocomplete([_group("alpha")])
    ctx = _FakeAutocompleteContext("delete_whole_group", "", {})

    await _run(ctx)

    assert ctx.responded == []
