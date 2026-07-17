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

"""Trials of Osiris — anchor producer for the ``trials`` followable.

Like ``weekly_reset``, the Trials post historically had *no* anchor producer: a human
hand-authored it and beacon mirrored it. This extension gives it the same hybrid
pipeline, built on :mod:`dd.anchor.hybrid_post_core`:

1. At Friday reset a cron seeds an uncrossposted **draft** (the :class:`TrialsContext`)
   to the ``trials_draft`` :class:`~dd.common.schemas.RotationData` row.
2. The team fills it — the featured maps and the bonus focus pool — through the
   owner-authenticated **web form** (``/trials``; ``/trials create`` links to it). Auth
   is enforced centrally by the Discord-OAuth middleware in ``web_auth.py``.
3. On publish the assembled post is crossposted to :data:`cfg.followables["trials"]`;
   beacon mirrors it to followers as usual.

The Trials post is effectively **fully manual**: the Bungie API does not expose the
weekly featured maps or the Saint-14 "bonus focus pool" (only the full focus pool), so
there are no API-seeded fields. ``Live until`` is the one derived value — the next
Tuesday reset, when the Trials weekend ends. The focus pool is manifest-linked (light.gg
deep links + weapon-type emoji) via the shared weapon pool + resolver.
"""

import asyncio
import dataclasses
import datetime as dt
import logging
import re
import typing as t
from pathlib import Path

import aiocron
import aiohttp.web
import aiosqlite
import hikari as h
import lightbulb as lb

from dd.hmessage import HMessage

from ...common import cfg, rotation_schema, schemas
from ...common.bot import CachedFetchBot
from ...common.components import cv2_error, cv2_notice, respond_cv2
from .. import hybrid_post_core, web
from ..embeds import substitute_user_side_emoji
from ..hybrid_post_core import (
    DraftMeta,
    HybridPostSpec,
    WeaponRef,
    build_cv2,
    current_reset_ts,
    next_reset_ts,
    resolve_weapon,
)
from . import bungie_api as api

logger = logging.getLogger(__name__)
loader = lb.Loader()

# ---------------------------------------------------------------------------
# Slugs + static chrome
# ---------------------------------------------------------------------------

#: RotationData slugs for the in-progress draft, carried-over config, and metadata.
DRAFT_SLUG = "trials_draft"
CONFIG_SLUG = "trials_config"
META_SLUG = "trials_meta"
#: The editor-managed loot pool + schedule (a rotation-editor type, Dares-style sets).
LOOT_SLUG = rotation_schema.TRIALS_LOOT_SLUG

#: The post's fixed title (a masked link with an italic "of"), verbatim from the
#: hand-authored posts.
TRIALS_TITLE = "[Trials *of* Osiris](https://kyber3000.com/Trialspost)"
#: The Rewards section — two static lines the team never varies.
TRIALS_REWARDS: tuple[str, ...] = (
    "All Trials weapons available",
    "Weapon Attunement available",
)
#: The static sign-off line (an ``### `` H3 header + the Kyber cheer emoji).
TRIALS_FOOTER_LINE = "### Good luck in your games!  :gscheer:"

# The Trials bonus-focus-pool rotation: a fixed loop of curated weapon sets. Trials
# cycles through these one per *active* weekend (Iron Banner "No Trials" weeks are
# skipped, which falls out naturally because the cursor only advances when a post is
# committed). The loop is now edited through the rotation editor (the ``trials_loot``
# type — a Dares-style set pool + looping schedule) and stored in its own RotationData
# row; this baked constant is the single source of truth for the editor's starting
# document AND this producer's fallback when that row is absent (re-exported from the
# schema layer so the two can't drift). Seeded from the "Trials Bonus Pools" tab of the
# rotation spreadsheet as a one-off — the bot never reads the sheet at runtime.
DEFAULT_LOOT_SETS = rotation_schema.TRIALS_DEFAULT_LOOT_SETS


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class TrialsContext:
    """Every fillable slot in the Trials of Osiris post.

    Round-trips through the ``trials_draft`` RotationData row so an edit session
    survives restarts and can be resumed by any owner.
    """

    reset_ts: int
    #: Featured map names, in post order (human-entered free text).
    featured_maps: list[str] = dataclasses.field(default_factory=list)
    #: This week's bonus focus-pool weapons (manifest-linked where resolvable).
    focus_pool: list[WeaponRef] = dataclasses.field(default_factory=list)
    image_url: str | None = None
    #: Optional ad-hoc info notes.
    notes: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, t.Any]:
        return {
            "reset_ts": self.reset_ts,
            "featured_maps": list(self.featured_maps),
            "focus_pool": [w.to_dict() for w in self.focus_pool],
            "image_url": self.image_url,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, d: t.Mapping[str, t.Any]) -> "TrialsContext":
        return cls(
            reset_ts=int(d["reset_ts"]),
            featured_maps=[str(m) for m in d.get("featured_maps") or []],
            focus_pool=[WeaponRef.from_dict(w) for w in d.get("focus_pool") or []],
            image_url=d.get("image_url"),
            notes=[str(n) for n in d.get("notes") or []],
        )


def _default_loot_sets() -> list[list[str]]:
    return [list(s) for s in DEFAULT_LOOT_SETS]


@dataclasses.dataclass
class TrialsConfig:
    """Carried-over data so each Friday's fresh draft starts pre-filled, not blank.

    Also holds the bonus-focus-pool rotation *cursor*: ``last_loot_set_index`` is the
    memory of the last set used, so the next draft defaults to the following set in the
    loop. ``-1`` = "none used yet" — the first draft is set 0. The loop itself (the pool
    of sets + the schedule) lives in the editor-managed ``trials_loot`` RotationData
    row, not here — see :func:`load_loot_rotation`.
    """

    default_image_url: str | None = None
    last_featured_maps: list[str] = dataclasses.field(default_factory=list)
    last_loot_set_index: int = -1

    def to_dict(self) -> dict[str, t.Any]:
        return {
            "default_image_url": self.default_image_url,
            "last_featured_maps": list(self.last_featured_maps),
            "last_loot_set_index": self.last_loot_set_index,
        }

    @classmethod
    def from_dict(cls, d: t.Mapping[str, t.Any] | None) -> "TrialsConfig":
        if not d:
            return cls()
        return cls(
            default_image_url=d.get("default_image_url"),
            last_featured_maps=[str(m) for m in d.get("last_featured_maps") or []],
            last_loot_set_index=int(d.get("last_loot_set_index", -1)),
        )


# ---------------------------------------------------------------------------
# Loot rotation (editor-managed pool + schedule; producer-owned cursor)
# ---------------------------------------------------------------------------


def _strip_weapon_type(value: str) -> str:
    """Drop a trailing ``" (Type)"`` the rotation editor's item autocomplete appends.

    The editor's set UI stores weapon values as ``"The Immortal (Submachine Gun)"``
    (type disambiguation), but :func:`resolve_weapon` matches a bare manifest name or a
    numeric hash. Stripping the suffix lets doc-sourced names resolve to manifest-linked
    WeaponRefs; a bare name (e.g. the baked default) passes through unchanged.
    """
    return re.sub(r"\s*\([^()]*\)\s*$", "", value).strip()


def _expand_loot_rotation(doc: t.Mapping[str, t.Any] | None) -> list[list[str]]:
    """Expand a ``trials_loot`` doc into the looping list of weapon-name lists.

    ``{sets: [{name, weapons}], schedule: [name, …]}`` → the ordered, looping list of
    weapon-name lists the cursor walks (a schedule entry naming no set is dropped — the
    editor's save gate blocks that, but be defensive). Falls back to the baked default
    loop when the doc is absent/empty, so the rotation works before anyone edits it.
    """
    if doc:
        by_name = {
            str(s.get("name", "")): [
                _strip_weapon_type(str(w)) for w in s.get("weapons") or []
            ]
            for s in doc.get("sets") or []
        }
        rotation = [by_name[n] for n in doc.get("schedule") or [] if n in by_name]
        if rotation:
            return rotation
    return _default_loot_sets()


async def load_loot_rotation() -> list[list[str]]:
    """The current loot loop, sourced from the editor-managed ``trials_loot`` doc."""
    return _expand_loot_rotation(await schemas.RotationData.get_data(LOOT_SLUG))


def _next_in_rotation(rotation: list[list[str]], last_index: int) -> list[str]:
    """The weapon-name list the next draft defaults to (set after ``last_index``)."""
    if not rotation:
        return []
    return rotation[(last_index + 1) % len(rotation)]


def _match_in_rotation(rotation: list[list[str]], names: t.Iterable[str]) -> int | None:
    """Index of the rotation entry equal to ``names`` (case/order-insensitive) or None.

    A committed post's focus pool is matched here to decide which set was "used".
    """
    wanted = {n.strip().lower() for n in names if n and n.strip()}
    if not wanted:
        return None
    for i, s in enumerate(rotation):
        if {n.strip().lower() for n in s} == wanted:
            return i
    return None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def load_config() -> TrialsConfig:
    return TrialsConfig.from_dict(await schemas.RotationData.get_data(CONFIG_SLUG))


async def save_config(config: TrialsConfig) -> None:
    await schemas.RotationData.set_data(CONFIG_SLUG, config.to_dict())


async def load_draft() -> TrialsContext | None:
    data = await schemas.RotationData.get_data(DRAFT_SLUG)
    return TrialsContext.from_dict(data) if data else None


async def save_draft(ctx: TrialsContext) -> None:
    await schemas.RotationData.set_data(DRAFT_SLUG, ctx.to_dict())


async def load_meta() -> DraftMeta:
    return DraftMeta.from_dict(await schemas.RotationData.get_data(META_SLUG))


async def save_meta(meta: DraftMeta) -> None:
    await schemas.RotationData.set_data(META_SLUG, meta.to_dict())


# ---------------------------------------------------------------------------
# Draft build (no API — fully manual, seeded from the carried-over config)
# ---------------------------------------------------------------------------


async def build_draft_context(config: TrialsConfig | None = None) -> TrialsContext:
    """A fresh draft: the reset boundary, carried-over maps + the NEXT loot set.

    The bonus focus pool defaults to the set after the last-used one (the editor-managed
    rotation loop); each name resolves to a manifest-linked :class:`WeaponRef`
    (light.gg), degrading to a plain name if the manifest is offline. Still editable.
    """
    config = config or await load_config()
    rotation = await load_loot_rotation()
    items = await get_weapon_items()
    focus_pool = [
        w
        for name in _next_in_rotation(rotation, config.last_loot_set_index)
        if (w := resolve_weapon(name, items))
    ]
    return TrialsContext(
        reset_ts=current_reset_ts(),
        featured_maps=list(config.last_featured_maps),
        focus_pool=focus_pool,
        image_url=config.default_image_url,
    )


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def build_body(ctx: TrialsContext) -> str:
    """The full post markdown, with ``:emoji:`` tokens still un-substituted."""
    lines: list[str] = [
        f"# {TRIALS_TITLE}",
        "",
        f"Live until <t:{next_reset_ts(ctx.reset_ts)}:f>",
    ]

    maps = [m for m in ctx.featured_maps if m]
    if maps:
        lines += ["### Featured Maps", ""]
        lines += [f"- {m}" for m in maps]

    lines += ["### Rewards", "", *TRIALS_REWARDS]

    pool = [w for w in ctx.focus_pool if w and w.name]
    if pool:
        lines += ["", "**This Week's Bonus Focus Pool**"]
        lines += [f"- {w.markdown()}" for w in pool]

    for note in ctx.notes:
        if note:
            lines += ["", f":info: {note}"]

    lines.append(TRIALS_FOOTER_LINE)
    return "\n".join(lines)


async def format_trials(ctx: TrialsContext, bot: CachedFetchBot) -> HMessage:
    """Render the context to a Components V2 :class:`HMessage`."""
    body = await substitute_user_side_emoji(bot, build_body(ctx))
    return build_cv2(body, ctx.image_url)


async def _render_for_spec(ctx: TrialsContext, bot: CachedFetchBot) -> HMessage:
    """``HybridPostSpec.render`` hook, indirecting through the module global so a test
    that monkeypatches ``format_trials`` is honoured by the shared publish core."""
    return await format_trials(ctx, bot)


def _now_reset_ts() -> int:
    """``HybridPostSpec.current_reset_ts`` hook: the current reset-period boundary.

    Trials reuses the weekly Tuesday ``current_reset_ts()`` as its period key even
    though a weekend runs Fri→Tue: a Friday post stamps the *preceding* Tuesday and
    stays "current" until the next Tuesday reset — exactly the live window. Indirects
    through the module global so a test monkeypatching ``current_reset_ts`` steers the
    shared route code.
    """
    return current_reset_ts()


def validate_post(ctx: TrialsContext) -> list[str]:
    """Problems that would make the post empty or break Components V2 limits."""
    problems: list[str] = []
    body = build_body(ctx)
    if len(body) > 3900:
        problems.append(
            f"Post is too long ({len(body)}/3900 chars) — trim some sections."
        )
    if not (any(m for m in ctx.featured_maps) or any(w for w in ctx.focus_pool)):
        problems.append(
            "Post looks empty — add at least a featured map or a focus-pool weapon."
        )
    if ctx.image_url and not ctx.image_url.startswith(("http://", "https://")):
        problems.append("Image URL must start with http:// or https://.")
    if "trials" not in cfg.followables:
        problems.append("No 'trials' entry in FOLLOWABLES — nowhere to publish.")
    return problems


# ---------------------------------------------------------------------------
# Manifest weapon pool (for the focus-pool picker + resolver)
# ---------------------------------------------------------------------------

_weapon_items: list[tuple[str, int, str, int, str]] | None = None
_weapon_items_lock = asyncio.Lock()


async def _build_weapon_items() -> list[tuple[str, int, str, int, str]]:
    try:
        path = await api._get_latest_manifest(schemas.BungieCredentials.api_key)
        async with aiosqlite.connect(path) as con:
            cur = await con.cursor()
            return await hybrid_post_core.iter_weapon_items(cur)
    except Exception:
        logger.warning("trials: manifest weapon-pool build failed", exc_info=True)
        return []


async def get_weapon_items() -> list[tuple[str, int, str, int, str]]:
    """Build (once) and cache the manifest weapon pool the focus picker searches."""
    global _weapon_items
    if _weapon_items is not None:
        return _weapon_items
    async with _weapon_items_lock:
        if _weapon_items is None:
            _weapon_items = await _build_weapon_items()
        return _weapon_items


# ---------------------------------------------------------------------------
# Web form — server-side context + bootstrap
# ---------------------------------------------------------------------------

_FORM_HTML_PATH = (
    Path(__file__).resolve().parent.parent / "web_static" / "trials_form.html"
)

#: The live bot, stashed by the StartedEvent listener so the routes can reach REST.
_bot: CachedFetchBot | None = None

#: Serialises read-modify-write of the shared draft doc (single bot process).
_draft_lock = asyncio.Lock()


async def _context_from_payload(payload: t.Mapping[str, t.Any]) -> TrialsContext:
    """Build a :class:`TrialsContext` from the form JSON, entirely server-side.

    The client is never trusted for security-relevant transforms: each focus-pool value
    is re-resolved server-side (a manifest hash or typed name -> WeaponRef) against the
    weapon pool, and the maps/notes are split per-line and trimmed.
    """
    ctx = TrialsContext(reset_ts=int(payload.get("reset_ts") or current_reset_ts()))
    ctx.featured_maps = [
        line.strip()
        for line in str(payload.get("maps_text", "")).splitlines()
        if line.strip()
    ]
    items = await get_weapon_items()
    ctx.focus_pool = [
        w
        for value in payload.get("focus_pool") or []
        if (w := resolve_weapon(str(value), items))
    ]
    ctx.image_url = str(payload.get("image_url", "")).strip() or None
    ctx.notes = [
        line.strip()
        for line in str(payload.get("notes_text", "")).splitlines()
        if line.strip()
    ]
    return ctx


async def _build_options() -> dict[str, t.Any]:
    """Option pools shipped in the page bootstrap and filtered client-side."""
    items = await get_weapon_items()
    return {
        "items": [
            {"name": name, "hash": hash_, "type": type_name, "rarity": rarity}
            for (name, hash_, type_name, _item_type, rarity) in items
        ],
    }


async def _form_loot_sets() -> tuple[list[dict[str, t.Any]], str | None]:
    """The named loot sets (resolved to manifest weapons) + this week's set name.

    Powers the form's "load a set" picker: sourced from the editor-managed
    ``trials_loot`` doc (falling back to the baked default doc), each set's weapon names
    are stripped of the editor's ``" (Type)"`` suffix and resolved to manifest-linked
    weapon refs so the client can hydrate them straight into the focus-pool picker.
    ``current`` is the set the cursor points at for this weekend — the one a fresh
    draft's focus pool defaults to — mirroring :func:`_expand_loot_rotation`'s schedule
    filtering so the "(this week)" hint matches the set the producer would pick.
    """
    doc = (
        await schemas.RotationData.get_data(LOOT_SLUG)
        or rotation_schema.trials_loot_default_doc()
    )
    items = await get_weapon_items()
    sets = [
        {
            "name": str(s.get("name", "")),
            "weapons": [
                w.to_dict()
                for name in s.get("weapons") or []
                if (w := resolve_weapon(_strip_weapon_type(str(name)), items))
            ],
        }
        for s in doc.get("sets") or []
    ]
    names = {s["name"] for s in sets}
    schedule = [str(n) for n in doc.get("schedule") or [] if str(n) in names]
    current = None
    if schedule:
        nxt = ((await load_config()).last_loot_set_index + 1) % len(schedule)
        current = schedule[nxt]
    return sets, current


async def _build_bootstrap(draft: TrialsContext, meta: DraftMeta) -> dict[str, t.Any]:
    """The page bootstrap JSON: the draft, weapon pool, toggles and lifecycle flags."""
    config = await load_config()
    loot_sets, current_loot_set = await _form_loot_sets()
    return {
        "draft": draft.to_dict(),
        "options": await _build_options(),
        # The editor-managed loot sets (resolved weapons) + which one is this weekend's,
        # for the form's "load a set" picker. Editing the pool itself happens in the
        # rotation editor (linked from the form); this is a convenience shortcut.
        "loot_sets": loot_sets,
        "current_loot_set": current_loot_set,
        "autopost_enabled": bool(await schemas.AutoPostSettings.get_trials_enabled()),
        "default_image_url": config.default_image_url or "",
        "accent_color": str(cfg.embed_default_color),
        # Whether a post already exists *for the current period* (Trials may skip a
        # week; False is a normal "no Trials post yet" state). Drives which action
        # buttons show: Create-* when there's none, Edit/Delete when there is.
        "post_this_period": meta.is_current(current_reset_ts()),
        "crossposted": meta.crossposted,
    }


def _record_loot_set_used(
    config: TrialsConfig, ctx: TrialsContext, rotation: list[list[str]]
) -> None:
    """Advance the rotation cursor to the set this published post used.

    Matches the post's focus pool against the loop's sets: a match (the usual case — the
    default IS a set, and a manual pick of another pool matches it) jumps the cursor to
    that set; a non-empty pool that matches nothing (a custom/edited pool) advances by
    one so the loop still progresses; an empty pool is a no-op. Called once per period
    (on the crosspost transition), so ``+ 1`` is from the previous period's set.
    """
    names = [w.name for w in ctx.focus_pool if w and w.name]
    if not names:
        return
    matched = _match_in_rotation(rotation, names)
    if matched is not None:
        config.last_loot_set_index = matched
    elif rotation:
        config.last_loot_set_index = (config.last_loot_set_index + 1) % len(rotation)


async def _advance_loot_cursor(ctx: TrialsContext) -> None:
    """``HybridPostSpec.on_published`` hook: record the published set as last-used.

    Fires once, only when a post goes live (uncrossposted -> crossposted) — so Iron
    Banner "No Trials" weekends (a cron draft that's deleted, never published) never
    advance the rotation, keeping it in sync with active weekends.
    """
    config = await load_config()
    rotation = await load_loot_rotation()
    _record_loot_set_used(config, ctx, rotation)
    await save_config(config)


async def _persist_carryover(
    payload: t.Mapping[str, t.Any], ctx: TrialsContext
) -> None:
    """Persist carried-over config on every committed post (Create/Edit).

    Stores this week's maps as the carry-over and the image as the default when the
    form's "use as default" box is ticked (an empty URL with the box ticked clears the
    default). The loot-set rotation cursor is advanced separately, only on publish, by
    :func:`_advance_loot_cursor` (the ``on_published`` hook).
    """
    config = await load_config()
    config.last_featured_maps = list(ctx.featured_maps)
    if payload.get("set_default_image"):
        config.default_image_url = ctx.image_url
    await save_config(config)


# ---------------------------------------------------------------------------
# Web routes — thin wrappers over the shared hybrid_post_core handlers
# ---------------------------------------------------------------------------
# Auth is enforced centrally by the web_auth middleware; these pass this producer's
# ``_SPEC`` and live ``_bot`` (read at call time) into the shared handlers.


async def _handle_form_get(request: aiohttp.web.Request) -> aiohttp.web.Response:
    return await hybrid_post_core.form_get(_SPEC, request, _bot)


async def _handle_create(request: aiohttp.web.Request) -> aiohttp.web.Response:
    return await hybrid_post_core.post_action(_SPEC, request, _bot, create=True)


async def _handle_edit(request: aiohttp.web.Request) -> aiohttp.web.Response:
    return await hybrid_post_core.post_action(_SPEC, request, _bot, create=False)


async def _handle_preview(request: aiohttp.web.Request) -> aiohttp.web.Response:
    return await hybrid_post_core.preview(_SPEC, request, _bot)


async def _handle_delete(request: aiohttp.web.Request) -> aiohttp.web.Response:
    return await hybrid_post_core.delete(_SPEC, request, _bot)


async def _handle_auto(request: aiohttp.web.Request) -> aiohttp.web.Response:
    return await hybrid_post_core.auto(_SPEC, request, _bot)


#: Wires this producer to the shared hybrid_post_core (built after every hook exists).
_SPEC = HybridPostSpec(
    followable_key="trials",
    post_noun="Trials post",
    current_reset_ts=_now_reset_ts,
    render=_render_for_spec,
    validate=validate_post,
    build_body=build_body,
    load_draft=load_draft,
    save_draft=save_draft,
    build_context=build_draft_context,
    context_from_payload=_context_from_payload,
    load_meta=load_meta,
    save_meta=save_meta,
    build_bootstrap=_build_bootstrap,
    persist_default_image=_persist_carryover,
    get_autopost=schemas.AutoPostSettings.get_trials_enabled,
    set_autopost=schemas.AutoPostSettings.set_trials,
    form_html_path=_FORM_HTML_PATH,
    draft_lock=_draft_lock,
    on_published=_advance_loot_cursor,
)


def register_trials_routes(app: aiohttp.web.Application) -> None:
    """Add the Trials web-form routes to the shared persistent app."""
    app.router.add_get("/trials", _handle_form_get)
    app.router.add_post("/trials/create", _handle_create)
    app.router.add_post("/trials/edit", _handle_edit)
    app.router.add_post("/trials/preview", _handle_preview)
    app.router.add_post("/trials/delete", _handle_delete)
    app.router.add_post("/trials/auto", _handle_auto)


web.register_routes(register_trials_routes)
web.register_card(
    web.Card(
        "Trials",
        "Compose & publish the Trials of Osiris post",
        "/trials",
    )
)


# ---------------------------------------------------------------------------
# Reset-weekend cron
# ---------------------------------------------------------------------------


async def run_trials_draft(bot: CachedFetchBot) -> None:
    """Build a fresh draft and post it as the weekend's *uncrossposted* channel post.

    A fresh ``DraftMeta`` (``message_id == 0``) means the post is created anew each
    Friday; publishing (the crosspost) stays manual. Trials may be skipped some
    weekends, so this only posts an *uncrossposted* draft the team can delete; and it
    no-ops if a post already exists this period (a manual Create beat the cron),
    rather than clobbering it with a duplicate.
    """
    async with _draft_lock:
        if (await load_meta()).is_current(current_reset_ts()):
            logger.info("trials: a post already exists this period; cron skips")
            return
        config = await load_config()
        ctx = await build_draft_context(config)
        meta = DraftMeta(
            status="draft",
            last_edited_ts=int(dt.datetime.now(tz=dt.UTC).timestamp()),
        )
        await save_draft(ctx)
        meta = await hybrid_post_core.post_or_edit_unpublished(_SPEC, bot, ctx, meta)
        await save_meta(meta)
        # NB: the cron posts UNCROSSPOSTED and does NOT advance the loot-set cursor —
        # the rotation only advances on an actual publish (_advance_loot_cursor), so a
        # weekend seeded then deleted (e.g. Iron Banner) doesn't consume a set.

    logger.info("trials: fresh draft posted (uncrossposted) for the new weekend")


# ---------------------------------------------------------------------------
# Slash command + startup
# ---------------------------------------------------------------------------


trials_group = lb.Group("trials", "Trials of Osiris post (owner only)")


@trials_group.register
class Create(
    lb.SlashCommand,
    name="create",
    description="Open the owner-only Trials web form",
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context) -> None:
        if not cfg.public_base_url:
            await respond_cv2(
                ctx,
                cv2_error(
                    "No editor link available",
                    "No public base URL is configured (set PUBLIC_BASE_URL or run on "
                    "Railway), so I can't mint a reachable edit link.",
                ),
                ephemeral=True,
            )
            return

        url = f"{cfg.public_base_url}/trials"
        container = cv2_notice(
            "Open the Trials form with the button below — you'll sign in with Discord "
            "the first time. Edit, preview, save, publish and toggle the autopost all "
            "from that page."
        )
        row = h.impl.MessageActionRowBuilder()
        row.add_component(h.impl.LinkButtonBuilder(url=url, label="Open Trials form"))
        container.add_component(row)
        await respond_cv2(ctx, container, ephemeral=True)


@loader.listener(h.StartedEvent)
async def _schedule_trials(
    event: h.StartedEvent, bot: CachedFetchBot = lb.di.INJECTED
) -> None:
    if not cfg.followables.get("trials"):
        return

    # Stash the live bot so the web form's routes can reach the REST client.
    global _bot
    _bot = bot

    # Prewarm the manifest weapon pool so the first form load is fast.
    asyncio.create_task(get_weapon_items())

    # Friday 17:00 UTC — Trials returns at the Friday reset. Enable/disable lives on the
    # web form's autopost toggle (POST /trials/auto -> AutoPostSettings.set_trials).
    @aiocron.crontab("0 17 * * FRI", start=True)
    # Testing: post every minute -> @aiocron.crontab("* * * * *", start=True)
    async def autopost_trials() -> None:
        if not await schemas.AutoPostSettings.get_trials_enabled():
            return
        await run_trials_draft(bot)


# The web form's routes are always registered (above); the slash command that links to
# the form is gated on the publish target (the trials followable) — the same gate that
# guards the autopost cron and the StartedEvent listener.
if cfg.followables.get("trials"):
    loader.command(trials_group)
