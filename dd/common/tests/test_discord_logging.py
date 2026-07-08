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

# Pure-logic unit tests for dd.common.discord_logging — no Discord I/O. Exercises the
# reference code surfaced by ``log_command_failure`` and the uncaught-error reply body
# via small fakes for the lightbulb pipeline exception and context.

import types

import pytest

from dd.common import discord_logging
from dd.common.utils import identity_for_exc, reference_code


def _fake_pipeline_exc(name: str, cause: BaseException):
    """A stand-in for ``ExecutionPipelineFailedException`` exposing only what
    ``log_command_failure`` reads: ``context.command_data.qualified_name`` and
    ``causes``."""
    context = types.SimpleNamespace(
        command_data=types.SimpleNamespace(qualified_name=name)
    )
    return types.SimpleNamespace(context=context, causes=[cause])


def test_log_command_failure_returns_matching_code(caplog):
    cause = ValueError("boom 42")
    exc = _fake_pipeline_exc("foo bar", cause)

    name, code = discord_logging.log_command_failure(exc)

    assert name == "foo bar"
    assert code == reference_code(identity_for_exc(cause))


@pytest.mark.asyncio
async def test_uncaught_error_reply_includes_reference_code(monkeypatch):
    cause = RuntimeError("kaboom")
    exc = _fake_pipeline_exc("thing", cause)
    expected = reference_code(identity_for_exc(cause))

    captured: dict[str, str] = {}
    ephemeral_flag = False

    def fake_cv2_error(title: str, body: str = ""):
        captured["title"] = title
        captured["body"] = body
        return object()

    async def fake_respond_cv2(context, component, *, ephemeral=False):
        nonlocal ephemeral_flag
        ephemeral_flag = ephemeral

    monkeypatch.setattr(discord_logging, "cv2_error", fake_cv2_error)
    monkeypatch.setattr(discord_logging, "respond_cv2", fake_respond_cv2)

    handled = await discord_logging._report_uncaught_command_error(exc)

    assert handled is True
    assert ephemeral_flag is True
    assert f"ref: `{expected}`" in captured["body"]
    assert "`/thing`" in captured["body"]
