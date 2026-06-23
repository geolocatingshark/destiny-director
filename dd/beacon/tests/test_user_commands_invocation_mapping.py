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

# Regression tests for ``_restore_invocation_mapping_defaults``, the workaround for a
# lightbulb 3.2.3 bug in ``Client.unregister`` that breaks adding a third command
# layer to a DB-backed user command. See the helper's docstring for the full story.

import collections
import types
import typing as t

import lightbulb as lb
from lightbulb.internal.constants import GLOBAL_COMMAND_KEY

from dd.beacon.extensions import user_commands as uc


def _fresh_mapping() -> collections.defaultdict:
    """A command-invocation mapping shaped exactly like lightbulb builds it:
    an outer defaultdict whose factory makes inner ``defaultdict``s."""
    return collections.defaultdict(lambda: collections.defaultdict(lambda: "COLL"))


def _corrupt_like_unregister(mapping: collections.defaultdict, group_name: str) -> None:
    """Replay the exact mutation ``Client.unregister`` performs for a group: rebuild
    each inner mapping as a *plain* dict comprehension, dropping the defaultdict
    factory and the group's existing subcommand paths."""
    for guild_id, inner in mapping.items():
        mapping[guild_id] = {
            path: collection
            for path, collection in inner.items()
            if not (len(path) > 1 and path[0] == group_name)
        }


def _client_with(mapping: object) -> lb.Client:
    # The helper only touches ``client._command_invocation_mapping``; a namespace
    # is enough. Cast so the duck-typed double satisfies the ``lb.Client`` signature.
    return t.cast(
        "lb.Client", types.SimpleNamespace(_command_invocation_mapping=mapping)
    )


def test_unregister_corruption_reproduces_keyerror_without_repair():
    """Document the upstream bug: after the group-unregister mutation the inner
    mapping is a plain dict, so a brand-new command path raises ``KeyError`` —
    which is what blows up syncing a third command layer."""
    mapping = _fresh_mapping()
    # Layer-2 command path populated by an earlier successful sync.
    mapping[GLOBAL_COMMAND_KEY][("test2", "test2")] = "COLL"

    _corrupt_like_unregister(mapping, "test2")

    # The defaultdict factory is gone, so a new (layer-3) path no longer auto-creates.
    new_path = ("test2", "test2", "test2")
    try:
        mapping[GLOBAL_COMMAND_KEY][new_path]
    except KeyError:
        pass
    else:  # pragma: no cover - guards against the precondition silently changing
        raise AssertionError("expected the corrupted mapping to raise KeyError")


def test_repair_restores_autocreation_after_corruption():
    """After the repair, the previously-corrupted inner mapping auto-creates missing
    paths again, so syncing a new command layer no longer raises."""
    mapping = _fresh_mapping()
    mapping[GLOBAL_COMMAND_KEY][("test2", "test2")] = "COLL"
    _corrupt_like_unregister(mapping, "test2")

    uc._restore_invocation_mapping_defaults(_client_with(mapping))

    inner = mapping[GLOBAL_COMMAND_KEY]
    assert isinstance(inner, collections.defaultdict)
    # The new layer-3 path now auto-creates instead of raising KeyError.
    assert inner[("test2", "test2", "test2")] == "COLL"


def test_repair_preserves_surviving_entries():
    """Repair must keep the entries the unregister mutation left behind (other
    top-level commands), not just restore auto-creation."""
    mapping = _fresh_mapping()
    mapping[GLOBAL_COMMAND_KEY][("other",)] = "KEEP"
    mapping[GLOBAL_COMMAND_KEY][("test2", "test2")] = "COLL"
    _corrupt_like_unregister(mapping, "test2")

    uc._restore_invocation_mapping_defaults(_client_with(mapping))

    assert mapping[GLOBAL_COMMAND_KEY][("other",)] == "KEEP"


def test_repair_is_noop_on_healthy_mapping():
    """A mapping whose inner entries are still defaultdicts is left untouched."""
    mapping = _fresh_mapping()
    inner_before = mapping[GLOBAL_COMMAND_KEY]
    inner_before[("test2",)] = "COLL"

    uc._restore_invocation_mapping_defaults(_client_with(mapping))

    # Same object, still a defaultdict, still auto-creating.
    assert mapping[GLOBAL_COMMAND_KEY] is inner_before
    assert isinstance(mapping[GLOBAL_COMMAND_KEY], collections.defaultdict)


def test_repair_bails_on_non_defaultdict_outer_mapping():
    """If the outer mapping is ever not a defaultdict, the helper bails out without
    raising so it degrades gracefully once the upstream bug is fixed."""
    plain = {GLOBAL_COMMAND_KEY: {("test2",): "COLL"}}
    uc._restore_invocation_mapping_defaults(_client_with(plain))
    # Unchanged.
    assert plain == {GLOBAL_COMMAND_KEY: {("test2",): "COLL"}}
