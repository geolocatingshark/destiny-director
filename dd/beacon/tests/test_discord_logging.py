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

import logging

from dd.common import discord_logging as dl

_FILE = "test_discord_logging.py"


def _record(name: str, level: int, msg: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord(name, level, _FILE, 1, msg, args, None)


def test_reference_formatter_tags_errors_with_alert_code():
    """ERROR+ lines gain ``[ref:CODE]`` matching the alert's reference code."""
    rec = _record("dd.error", logging.ERROR, "boom %s", "x")
    out = dl._ReferenceFormatter("%(levelname).1s %(name)s | %(message)s").format(rec)

    expected = dl.reference_code(dl._record_identity(rec))
    assert f"[ref:{expected}]" in out
    # The code is also stamped on the record for the Discord handler to reuse.
    assert getattr(rec, "dd_reference", None) == expected


def test_reference_formatter_leaves_info_and_ignored_loggers_untouched():
    info = _record("dd.x", logging.INFO, "hi")
    assert "[ref:" not in dl._ReferenceFormatter("%(message)s").format(info)

    # hikari/lightbulb/etc. are never forwarded, so they are not tagged either.
    ignored = _record("hikari.rest", logging.ERROR, "noisy")
    assert "[ref:" not in dl._ReferenceFormatter("%(message)s").format(ignored)


def test_emit_carries_operation_and_reference_onto_alert_record():
    """``dd_operation`` and the reference code reach the queued ``_AlertRecord``."""
    handler = dl.DiscordLogHandler.__new__(dl.DiscordLogHandler)
    logging.Handler.__init__(handler, level=logging.ERROR)

    queued: list[dl._AlertRecord] = []
    handler._queue = type("Q", (), {"put_nowait": lambda self, x: queued.append(x)})()
    handler._overflow_warned = False

    rec = _record("dd.error", logging.ERROR, "Error reference: %s", "ABC")
    rec.dd_operation = "Mirror update"
    handler.emit(rec)

    (alert,) = queued
    assert alert.operation == "Mirror update"
    assert alert.reference == dl.reference_code(dl._record_identity(rec))
