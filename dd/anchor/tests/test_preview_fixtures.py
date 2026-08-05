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

"""The Python half of the shared preview corpus — the DATA, not the drawing.

There is one renderer now (``web_static/cv2_render.js``), so rendering is asserted on
the JS side (``web_static/tests/preview_fixtures.test.js``). What is still Python's job
is everything that decides *what* gets drawn, and this pins each of those against the
fixtures:

- :func:`cv2_render.diff_payload` — the structural alignment, as annotations
- :func:`cv2_nodes.sanitize_for_preview` — the builder's mid-construction downgrade
- :func:`hybrid_post_core.post_spec_nodes` — a hybrid post's node tree

Plus one thing neither side would otherwise own: :func:`test_no_fixture_expects_an
_executable_sink` parses every recorded expectation and holds it to the tag / attribute
/ URL whitelist. It audits the *frozen strings* rather than a live render, which is the
point — the JS tests assert the renderer produces exactly those strings, so auditing
them audits the renderer, and it keeps working with no renderer here at all.

Regenerating is two steps, in this order, because the JS render reads the data fields
this writes::

    UPDATE_PREVIEW_FIXTURES=1 uv run --env-file .env python -m pytest \\
        dd/anchor/tests/test_preview_fixtures.py
    UPDATE_PREVIEW_FIXTURES=1 node --test \\
        dd/anchor/web_static/tests/preview_fixtures.test.js

Read the diff both times. That diff is the entire value of the corpus.
"""

import json
import os
import pathlib
import re
import typing as t
from html.parser import HTMLParser

import pytest

from dd.anchor import cv2_nodes, cv2_render, hybrid_post_core

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent.parent / "preview_fixtures"

UPDATE = bool(os.environ.get("UPDATE_PREVIEW_FIXTURES"))

#: Every tag the renderer is trusted to emit. The leaf whitelist is
#: ``{span, strong, em, code, a, img}``; the rest are structural wrappers
#: (``div``/``hr``) and the diff's change marks (``ins``/``del``).
ALLOWED_TAGS = frozenset(
    {"div", "span", "strong", "em", "code", "a", "img", "hr", "ins", "del"}
)

#: Every attribute those tags may carry. Notably absent: anything ``on*``, which is why
#: this is an allowlist rather than a denylist — a new handler attribute cannot be
#: introduced without failing here first.
ALLOWED_ATTRS = frozenset(
    {"class", "style", "src", "alt", "href", "target", "rel", "loading"}
)

#: The only ``style`` the renderer writes. Anything else means a colour reached the
#: attribute without being validated to an int first.
_STYLE_OK = re.compile(r"^border-left-color:#[0-9a-f]{6}$")


def _load(path: pathlib.Path) -> dict[str, t.Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_files() -> list[pathlib.Path]:
    return sorted(FIXTURE_DIR.glob("*.json"))


def _cases() -> list[tuple[pathlib.Path, dict[str, t.Any]]]:
    return [(p, c) for p in _fixture_files() for c in _load(p)["cases"]]


def _case_id(entry: tuple[pathlib.Path, dict[str, t.Any]]) -> str:
    path, case = entry
    return f"{path.stem}:{case['name']}"


def _derive(case: dict[str, t.Any]) -> None:
    """Fill in the data fields the JS side renders from, for one case."""
    how = case["render"]
    if how == "diff":
        case["annotated"] = cv2_render.diff_payload(
            case["payload"], case["kind"], case["old_payload"], case["old_kind"]
        )
    elif how == "authored":
        case["sanitized"] = cv2_nodes.sanitize_for_preview(
            case["payload"].get("components") or []
        )
    elif how == "post_spec":
        spec = case["spec"]
        case["nodes"] = hybrid_post_core.post_spec_nodes(
            hybrid_post_core.PostSpec.cv2(
                body=spec.get("body", ""),
                image_url=spec.get("image_url"),
                buttons=[tuple(b) for b in spec.get("buttons") or []],
            )
        )


@pytest.mark.parametrize("entry", _cases(), ids=_case_id)
def test_fixture_data_is_what_the_producer_makes(
    entry: tuple[pathlib.Path, dict[str, t.Any]],
) -> None:
    """The recorded data still matches what its producer emits.

    Cases whose ``render`` produces no server-side data (``markdown``, ``snapshot``)
    have nothing to check here — the payload in the fixture *is* the input, and drawing
    it is the JS side's business.
    """
    _, case = entry
    recorded = {k: case.get(k) for k in ("annotated", "sanitized", "nodes")}
    fresh = dict(case)
    _derive(fresh)

    for key, was in recorded.items():
        if was is None and fresh.get(key) is None:
            continue
        assert fresh.get(key) == was, f"{case['name']}: {key} drifted"


def test_every_case_name_is_unique() -> None:
    """Names are the corpus' cross-language join key, so a collision is a real bug."""
    names = [f"{p.stem}:{c['name']}" for p, c in _cases()]
    assert len(names) == len(set(names)), "duplicate fixture case name"


def test_every_case_has_a_frozen_expectation() -> None:
    for path, case in _cases():
        assert isinstance(case.get("expected_html"), str), (
            f"{path.stem}:{case['name']} has no expected_html — regenerate it"
        )


class _Auditor(HTMLParser):
    """Collect every tag and attribute a recorded expectation contains.

    Parsing beats grepping: an escaped ``&lt;img onerror=…&gt;`` is *text*, which a
    regex over the raw string cannot tell apart from a real attribute — and that
    difference is exactly what the escaping is for.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.problems: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ALLOWED_TAGS:
            self.problems.append(f"tag <{tag}>")
        for name, value in attrs:
            if name not in ALLOWED_ATTRS:
                self.problems.append(f"attribute {name!r} on <{tag}>")
                continue
            if name in ("href", "src") and not str(value or "").startswith(
                ("http://", "https://")
            ):
                self.problems.append(f"non-http(s) {name}={value!r} on <{tag}>")
            if name == "style" and not _STYLE_OK.match(str(value or "")):
                self.problems.append(f"unexpected style={value!r} on <{tag}>")


def test_no_fixture_expects_an_executable_sink() -> None:
    """Every frozen expectation stays inside the tag/attribute/URL whitelist.

    Over the *whole* corpus, not just the injection probes — a benign-looking fixture is
    exactly where an escaping regression would hide. This is the property that has to
    hold now that the renderer draws other people's posts in the reader's browser.
    """
    offenders: list[str] = []
    for path, case in _cases():
        auditor = _Auditor()
        auditor.feed(case.get("expected_html") or "")
        auditor.close()
        offenders += [f"{path.stem}:{case['name']}: {p}" for p in auditor.problems]
    assert not offenders, "a frozen expectation escapes the whitelist:\n" + "\n".join(
        offenders
    )


def test_update_mode_writes_the_data_fields() -> None:
    """Under ``UPDATE_PREVIEW_FIXTURES=1``, refresh what the server produces.

    Kept as a test rather than a script so regeneration runs through the same producers
    the assertions use — a generator that drifted from its checker would defeat the
    corpus. ``expected_html`` is the JS side's to write; run it second.
    """
    if not UPDATE:
        pytest.skip("set UPDATE_PREVIEW_FIXTURES=1 to regenerate")
    for path in _fixture_files():
        data = _load(path)
        for case in data["cases"]:
            _derive(case)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
