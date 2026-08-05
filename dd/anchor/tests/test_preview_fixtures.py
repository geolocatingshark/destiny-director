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

"""The shared golden corpus — what a preview renders to, frozen.

``dd/anchor/preview_fixtures/*.json`` holds render *inputs* plus the HTML today's Python
renderer produces for each. This module asserts the Python side still matches; the JS
side asserts the same corpus once the shared renderer lands
(``plans/preview_renderer_unification.md``). Two implementations reading one corpus is
what makes the port provably behaviour-preserving instead of hopefully so.

Regenerate expectations deliberately, never casually::

    UPDATE_PREVIEW_FIXTURES=1 uv run --env-file .env python -m pytest \\
        dd/anchor/tests/test_preview_fixtures.py

and read the resulting diff — that diff is the entire value of the corpus.

The XSS cases are not decoration. The mirror log renders *other people's* captured
posts, so every probe in ``xss.json`` is attacker-controlled in production;
:func:`test_no_fixture_emits_an_executable_sink` holds the whole corpus to that line at
once, so a new fixture cannot quietly introduce an unescaped sink.
"""

import datetime as dt
import json
import os
import pathlib
import re
import types
import typing as t
from html.parser import HTMLParser

import hikari as h
import pytest

from dd.anchor import cv2_html, cv2_render, hybrid_post_core

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent.parent / "preview_fixtures"

#: The corpus' frozen clock. ``<t:…:R>`` renders a relative string off the render-time
#: clock, so without pinning it every regeneration would churn. Absolute format letters
#: need no freezing, but they share the substituter, so one patch covers both.
NOW = dt.datetime(2026, 7, 30, 17, 0, 0, tzinfo=dt.UTC)

UPDATE = bool(os.environ.get("UPDATE_PREVIEW_FIXTURES"))

#: Every tag the renderers are trusted to emit. The leaf whitelist is
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

#: The only ``style`` the renderers write. Anything else means a colour reached the
#: attribute without being validated to an int first.
_STYLE_OK = re.compile(r"^border-left-color:#[0-9a-f]{6}$")


def _emoji(url: str) -> h.Emoji:
    """A stand-in for :class:`hikari.Emoji`.

    ``_html_emoji_substituter`` duck-types the object (``getattr(emoji, "url", "")``),
    so a namespace is enough; the cast keeps call sites typed without building a real
    hikari emoji. Same trick as ``test_cv2_html.py``.
    """
    return t.cast(h.Emoji, types.SimpleNamespace(url=url))


def _load(path: pathlib.Path) -> dict[str, t.Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_files() -> list[pathlib.Path]:
    return sorted(FIXTURE_DIR.glob("*.json"))


def _cases() -> list[tuple[pathlib.Path, dict[str, t.Any], dict[str, t.Any]]]:
    """Every case in the corpus as ``(file, case, file-level defaults)``."""
    out = []
    for path in _fixture_files():
        data = _load(path)
        defaults = {"emoji": data.get("emoji") or {}}
        for case in data["cases"]:
            out.append((path, case, defaults))
    return out


def _render(case: dict[str, t.Any], defaults: dict[str, t.Any]) -> str:
    """Render one case through the entry point its ``render`` field names."""
    emoji_map = case.get("emoji", defaults["emoji"])
    emoji_dict = {name: _emoji(url) for name, url in emoji_map.items()}
    how = case["render"]

    if how == "markdown":
        # The narrowest cross-language seam: one text leaf, no wrappers.
        # `cv2_model.js`'s renderMd asserts these same strings, which is what pins
        # the two markdown implementations to a single output.
        return cv2_render._render_markdown(
            case["content"], hybrid_post_core._html_emoji_substituter(emoji_dict)
        )
    if how == "snapshot":
        return cv2_render.render_snapshot(case["payload"], case["kind"])
    if how == "authored":
        nodes = case["payload"].get("components") or []
        return cv2_html.render_cv2_nodes_html(nodes, emoji_dict)
    if how == "diff":
        return cv2_render.render_diff(
            case["payload"], case["kind"], case["old_payload"], case["old_kind"]
        )
    if how == "post_spec":
        spec = case["spec"]
        post = hybrid_post_core.PostSpec.cv2(
            body=spec.get("body", ""),
            image_url=spec.get("image_url"),
            buttons=[tuple(b) for b in spec.get("buttons") or []],
        )
        return hybrid_post_core.render_post_spec(post, emoji_dict)
    raise AssertionError(f"Unknown render mode {how!r} in fixture {case['name']!r}")


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the relative-timestamp clock for the whole module.

    ``_render_inline`` calls ``_format_ts`` without a ``now``, so ``<t:…:R>`` would read
    the wall clock and make the corpus unreproducible. Binding the default here rather
    than editing the renderer keeps this a *test* concern — and the JS renderer already
    threads ``now`` as an explicit parameter, which is the shape the port keeps.
    """
    real = hybrid_post_core._format_ts

    def frozen(unix: int, fmt: str, now: dt.datetime | None = None) -> str:
        return real(unix, fmt, now or NOW)

    monkeypatch.setattr(hybrid_post_core, "_format_ts", frozen)


def _case_id(entry: tuple[pathlib.Path, dict[str, t.Any], dict[str, t.Any]]) -> str:
    path, case, _ = entry
    return f"{path.stem}:{case['name']}"


@pytest.mark.parametrize("entry", _cases(), ids=_case_id)
def test_fixture_renders_to_its_frozen_html(
    entry: tuple[pathlib.Path, dict[str, t.Any], dict[str, t.Any]],
) -> None:
    _, case, defaults = entry
    got = _render(case, defaults)
    if UPDATE:
        pytest.skip("regenerating expectations")
    assert "expected_html" in case, (
        f"{case['name']} has no expected_html — run with UPDATE_PREVIEW_FIXTURES=1"
    )
    assert got == case["expected_html"]


def test_every_case_name_is_unique() -> None:
    """Names are the corpus' cross-language join key, so a collision is a real bug."""
    names = [f"{p.stem}:{c['name']}" for p, c, _ in _cases()]
    assert len(names) == len(set(names)), "duplicate fixture case name"


class _Auditor(HTMLParser):
    """Collect every tag and attribute a render emitted.

    Parsing beats grepping here: an escaped ``&lt;img onerror=…&gt;`` arrives as *text*,
    which a regex over the raw string cannot tell apart from a real attribute — and
    that difference is exactly what the escaping is for. Anything the parser reports as
    a tag genuinely is one.
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


def test_no_fixture_emits_an_executable_sink() -> None:
    """Every render stays inside the tag/attribute/URL whitelist.

    Run over the *whole* corpus, not just ``xss.json`` — a benign-looking fixture is
    exactly where an escaping regression would hide. This is the property the port has
    to preserve when the renderer moves to the client and starts drawing other people's
    posts in the reader's browser.
    """
    offenders: list[str] = []
    for path, case, defaults in _cases():
        auditor = _Auditor()
        auditor.feed(_render(case, defaults))
        auditor.close()
        offenders += [f"{path.stem}:{case['name']}: {p}" for p in auditor.problems]
    assert not offenders, "rendered output escaped its whitelist:\n" + "\n".join(
        offenders
    )


def test_update_mode_writes_expectations() -> None:
    """Under ``UPDATE_PREVIEW_FIXTURES=1``, rewrite every file's expectations.

    Kept as a test rather than a separate script so regeneration runs through the same
    frozen clock and the same entry-point dispatch the assertions use — a generator that
    drifted from the checker would defeat the corpus.
    """
    if not UPDATE:
        pytest.skip("set UPDATE_PREVIEW_FIXTURES=1 to regenerate")
    for path in _fixture_files():
        data = _load(path)
        defaults = {"emoji": data.get("emoji") or {}}
        for case in data["cases"]:
            case["expected_html"] = _render(case, defaults)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
