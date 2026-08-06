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

"""Tests for the shared post core (reset maths, PostSpec, post_spec_nodes).

The rendering these used to assert moved to the shared golden corpus
(``dd/anchor/preview_fixtures``), which holds the Python and JavaScript renderers
to one output rather than checking either alone."""

import asyncio
import dataclasses
import typing as t

import hikari as h
import pytest

from dd.anchor import hybrid_post_core as hpc


def test_hybrid_post_spec_has_no_autopost_hooks() -> None:
    # weekly_reset/trials no longer carry a reset-day autopost toggle, so the shared
    # spec dropped its get_autopost/set_autopost hooks entirely.
    fields = {f.name for f in dataclasses.fields(hpc.HybridPostSpec)}
    assert "get_autopost" not in fields
    assert "set_autopost" not in fields


def test_core_has_no_auto_route_handler() -> None:
    # The POST /{prefix}/auto handler that wrote the toggle is removed.
    assert not hasattr(hpc, "auto")


def test_autopostsettings_has_no_weekly_or_trials_toggle() -> None:
    from dd.common import schemas

    aps = schemas.AutoPostSettings
    for name in (
        "get_weekly_reset_enabled",
        "set_weekly_reset",
        "get_trials_enabled",
        "set_trials",
    ):
        assert not hasattr(aps, name), name
    # The generic accessors and other feeds' toggles are untouched.
    assert hasattr(aps, "get_enabled") and hasattr(aps, "get_iron_banner_enabled")


def test_postspec_cv2_factory_and_from_payload() -> None:
    direct = hpc.PostSpec.cv2("# Hi", "https://ex.com/a.png")
    assert direct.kind == "cv2"
    assert direct.body == "# Hi" and direct.image_url == "https://ex.com/a.png"

    # from_payload defaults to cv2 and coerces a blank/missing image to None.
    parsed = hpc.PostSpec.from_payload({"body": "# Hi", "image_url": ""})
    assert parsed == hpc.PostSpec.cv2("# Hi", None)
    assert hpc.PostSpec.from_payload({}) == hpc.PostSpec.cv2("", None)
    assert hpc.PostSpec.from_payload({"kind": "cv2", "body": "x"}).body == "x"


def test_postspec_from_payload_rejects_unknown_kind() -> None:
    # The embed kind (and any other) isn't renderable yet — surfaced as ValueError so a
    # route can 422 it.
    with pytest.raises(ValueError, match="Unsupported post kind"):
        hpc.PostSpec.from_payload({"kind": "embed", "title": "x"})


def test_footer_button_specs() -> None:
    from dd.common import components as c

    # Guides first, then the standard Support button.
    assert c.footer_button_specs(guides=[("Guide", "https://g.example")]) == [
        ("Guide", "https://g.example"),
        ("Support Us", c.KOFI_URL),
    ]
    # No guides -> just the Support button (e.g. Portal Ops / Weekly Reset).
    assert c.footer_button_specs() == [("Support Us", c.KOFI_URL)]
    # A row caps at 5 buttons, so at most 4 guides.
    with pytest.raises(ValueError):
        c.footer_button_specs(guides=[("a", "https://x")] * 5)


# --- post_spec_nodes ------------------------------------------------------------------


def _drop_defaults(value: t.Any) -> t.Any:
    """Strip hikari's explicit defaults so two descriptions of a post compare.

    ``build_cv2`` goes through hikari's builders, which spell out ``spoiler: false`` and
    ``disabled: false`` and hand back URL objects; ``post_spec_nodes`` writes the raw
    JSON Discord actually needs. Neither difference is a difference in the post.
    """
    defaults = {"spoiler": False, "disabled": False}
    if isinstance(value, dict):
        return {
            k: _drop_defaults(v)
            for k, v in value.items()
            if not (k in defaults and v == defaults[k])
        }
    if isinstance(value, list):
        return [_drop_defaults(v) for v in value]
    if isinstance(value, str):
        return value
    return str(value) if type(value).__name__ == "URL" else value


@pytest.mark.parametrize(
    "body,image,buttons",
    [
        ("just a body", None, ()),
        ("with an image", "https://example.com/i.png", ()),
        ("with buttons", None, (("Guide", "https://example.com/g"),)),
        (
            "everything",
            "https://example.com/i.png",
            (("Guide", "https://example.com/g"), ("Support", "https://example.com/s")),
        ),
    ],
)
def test_post_spec_nodes_matches_build_cv2(
    body: str, image: str | None, buttons: tuple
) -> None:
    """The preview's tree IS the post's tree.

    This is the pin that makes retiring the old ``.post-*`` previewer safe: rather than
    a second markup vocabulary approximating the post, the previewer renders the very
    node list ``build_cv2`` sends. If the two ever diverge, the preview stops being a
    preview — so compare them directly, on every shape a producer emits.
    """
    live, _ = hpc.build_cv2(body, image, buttons=buttons).components[0].build()
    spec = hpc.PostSpec.cv2(body, image, buttons=buttons)

    assert _drop_defaults(live) == _drop_defaults(hpc.post_spec_nodes(spec)[0])


@pytest.mark.parametrize(
    "image,kinds",
    [
        # Text, then the gallery — the order build_cv2 sends. Placement used to be the
        # previewer's business; it is the post's now.
        ("https://ex.com/a.png?x=1&y", [10, 12]),
        (None, [10]),
        # Matching the renderer, which refuses a non-http(s) media URL — better an
        # absent image in the preview than one the post will not carry.
        ("javascript:alert(1)", [10]),
        ("ftp://example.com/i.png", [10]),
    ],
)
def test_post_spec_nodes_places_the_image_and_rejects_bad_urls(
    image: str | None, kinds: list[int]
) -> None:
    spec = hpc.PostSpec.cv2("# Title", image)
    assert [c["type"] for c in hpc.post_spec_nodes(spec)[0]["components"]] == kinds


# --- resolve_weapon -------------------------------------------------------------------


@pytest.mark.parametrize("value", ["²", "³", "①", "⑵"])
def test_resolve_weapon_survives_a_non_decimal_digit(value: str) -> None:
    """A digit `int()` refuses must not reach it.

    ``str.isdigit()`` is true for superscripts and enclosed forms; ``int()`` only takes
    decimal ones. The gap used to raise ValueError out of the weekly-reset and trials
    forms, where a free-typed weapon name is the intended fallback — so the interesting
    assertion is that these resolve to a plain name rather than blowing up.
    """
    assert hpc.resolve_weapon(value, []) == hpc.WeaponRef(name=value)


def test_resolve_weapon_still_matches_a_real_hash() -> None:
    items: list[hpc.WeaponItem] = [
        ("Null Composure", 222, "Fusion Rifle", 3, "legendary")
    ]
    assert hpc.resolve_weapon("222", items) == hpc.WeaponRef(
        "Null Composure", 222, hpc.api.likely_emoji_name("Fusion Rifle")
    )
    # Arabic-Indic digits are decimal, so int() takes them — but nobody types those
    # meaning a manifest hash, so they stay a name.
    assert hpc.resolve_weapon("٢٢٢", items) == hpc.WeaponRef(name="٢٢٢")


# --- reconcile_missing_post -----------------------------------------------------------
# A post deleted in Discord has to be RETIRED from the record, not merely reported: the
# form's Edit/Create split and post_action's 409 guard both read `meta.is_current()`, so
# an answer that lives only in the render path offers a Create the server then refuses.
# The probe runs outside `draft_lock` (a REST call), so the locked re-read may find a
# different record than the one probed — hence a DraftMeta return rather than a bool:
# "gone, but a different post now exists" has no truthy answer.


class _FakeBot:
    """Only what reconcile_missing_post touches — one fetch_message that can fail."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def fetch_message(self, _channel_id: int, _message_id: int) -> object:
        self.calls += 1
        if self.error:
            raise self.error
        return object()


def _spec_for(meta: "hpc.DraftMeta", saved: list["hpc.DraftMeta"]) -> t.Any:
    """A stand-in spec exposing just the meta accessors and the draft lock."""

    class _Spec:
        followable_key = "weekly_reset"
        channel_id = 123
        draft_lock = asyncio.Lock()

        async def load_meta(self) -> hpc.DraftMeta:
            return meta

        async def save_meta(self, m: hpc.DraftMeta) -> None:
            saved.append(m)

    return _Spec()


@pytest.mark.asyncio
async def test_reconcile_retires_and_persists_a_deleted_post() -> None:
    meta = hpc.DraftMeta(
        message_id=7, reset_ts=99, crossposted=True, status="published"
    )
    saved: list[hpc.DraftMeta] = []
    bot = t.cast(t.Any, _FakeBot(h.NotFoundError(url="u", headers={}, raw_body=b"")))

    got = await hpc.reconcile_missing_post(_spec_for(meta, saved), meta, bot)
    # Persisted, so post_action's own is_current() check agrees with the form.
    assert saved and saved[0].message_id == 0
    assert saved[0].reset_ts == 0
    assert saved[0].crossposted is False
    assert saved[0].status == "draft"
    # And the returned meta is the retired record, so this request renders from it.
    assert got.is_current(99) is False
    assert got.message_id == 0


@pytest.mark.asyncio
async def test_reconcile_leaves_the_record_alone_when_discord_is_unhappy() -> None:
    # A rate limit or a transport blip is not evidence of deletion; flipping the form
    # into offering Create there would risk a second post for the period.
    meta = hpc.DraftMeta(message_id=7, reset_ts=99, status="posted")
    saved: list[hpc.DraftMeta] = []

    bot = t.cast(t.Any, _FakeBot(RuntimeError("429")))
    got = await hpc.reconcile_missing_post(_spec_for(meta, saved), meta, bot)
    assert got is meta
    assert not saved
    assert meta.message_id == 7


@pytest.mark.asyncio
async def test_reconcile_does_not_probe_without_a_bot() -> None:
    meta = hpc.DraftMeta(message_id=7, reset_ts=99, status="posted")
    saved: list[hpc.DraftMeta] = []

    got = await hpc.reconcile_missing_post(_spec_for(meta, saved), meta, None)
    assert got is meta
    assert not saved


@pytest.mark.asyncio
async def test_reconcile_adopts_a_newer_record_instead_of_retiring_it() -> None:
    # Between the (unlocked) fetch 404ing and the locked re-read, another tab or the
    # cron created a NEW post: the persisted record now names a different message. The
    # newer record must be left alone AND handed back, so the form renders Edit for the
    # post that now exists rather than a Create post_action would 409.
    stale = hpc.DraftMeta(message_id=7, reset_ts=99, status="posted")
    fresh = hpc.DraftMeta(message_id=8, reset_ts=99, status="posted")
    saved: list[hpc.DraftMeta] = []
    bot = t.cast(t.Any, _FakeBot(h.NotFoundError(url="u", headers={}, raw_body=b"")))

    got = await hpc.reconcile_missing_post(_spec_for(fresh, saved), stale, bot)
    assert got is fresh  # render from what the locked read established
    assert not saved  # the newer record is not touched
    assert stale.message_id == 7  # nor is the caller's stale copy wiped
