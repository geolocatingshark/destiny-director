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

"""Shared core for the anchor's *hybrid* followable producers.

A "hybrid" post (e.g. the Weekly Reset Overview, Trials of Osiris) is one a reset-day
cron seeds as an uncrossposted draft, a team member fills through an owner-authenticated
web form, and publishing crossposts so beacon mirrors it to followers. Every such post
shares the same machinery; this module is that machinery, factored out of
``extensions/weekly_reset.py`` so a second producer (``extensions/trials.py``) reuses it
instead of copying it.

This module lives OUTSIDE ``extensions/`` on purpose: the extension loader discovers
loadable modules with ``pkgutil.iter_modules`` over ``dd.anchor.extensions`` only, so a
core module here is never mistaken for a loadable extension. It imports only lower
layers (``cfg``, ``bungie_api``, ``HMessage``) and never a producer module, so there is
no import cycle.

Auth is deliberately absent: every anchor web surface is gated centrally by the
Discord-OAuth middleware in ``extensions/web_auth.py`` (which also does the cross-origin
check on unsafe methods), so producers — and this core — carry no session/cookie/origin
code.
"""

import asyncio
import dataclasses
import datetime as dt
import html
import json
import logging
import re
import typing as t
from pathlib import Path

import aiohttp.web
import aiosqlite
import hikari as h

from dd.hmessage import HMessage

from ..common import cfg, schemas
from ..common.bot import CachedFetchBot
from ..common.utils import fetch_emoji_dict, re_user_side_emoji
from . import utils
from .extensions import bungie_api as api

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Reset-time boundaries (deterministic, no API)
# ---------------------------------------------------------------------------

#: A known Tuesday 17:00 UTC weekly-reset boundary (matches beacon's weekly_reset ref).
REFERENCE_RESET = dt.datetime(2023, 7, 18, 17, tzinfo=dt.UTC)
WEEK = dt.timedelta(days=7)

#: Small-text footer appended below every hybrid post's body by :func:`build_cv2`.
FOOTER = "-# via Destiny Director (Kyber)"


def current_reset_ts(now: dt.datetime | None = None) -> int:
    """Unix ts of the reset boundary for the week containing ``now`` (Tue 17:00 UTC)."""
    now = now or dt.datetime.now(tz=dt.UTC)
    weeks = (now - REFERENCE_RESET) // WEEK
    return int((REFERENCE_RESET + weeks * WEEK).timestamp())


def next_reset_ts(reset_ts: int) -> int:
    """First reset boundary strictly after ``reset_ts`` — i.e. the next Tuesday.

    ``reset_ts`` is the *current* week's boundary (which drives the rotators), so
    this is the moment the post's content resets, shown on the ``Resets:`` line.
    """
    return reset_ts + int(WEEK.total_seconds())


def rotator_index(anchor_ts: int, reset_ts: int, length: int) -> int:
    """Which cycle entry is active this week (weeks since anchor, mod list length)."""
    if length <= 0:
        return 0
    weeks = (reset_ts - anchor_ts) // int(WEEK.total_seconds())
    return weeks % length


def compute_rotator(
    pairs: t.Sequence[tuple[str, str]], anchor_ts: int, reset_ts: int
) -> tuple[str, str]:
    if not pairs:
        return ("", "")
    return pairs[rotator_index(anchor_ts, reset_ts, len(pairs))]


# ---------------------------------------------------------------------------
# Weapon slot
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class WeaponRef:
    """A weapon slot: enough to render a light.gg-linked, emoji-prefixed line.

    Derived weapons carry a ``hash`` (so we can deep-link light.gg and infer the
    weapon-type emoji); hand-typed weapons may have no hash (plain text, no link).
    """

    name: str
    hash: int | None = None
    #: weapon-type emoji name (e.g. "pulse_rifle"); only needed for the Zavala line.
    emoji_name: str | None = None

    @property
    def lightgg_url(self) -> str | None:
        return f"https://light.gg/db/items/{self.hash}" if self.hash else None

    def markdown(self) -> str:
        """``[Name](url)`` when we have a hash, else plain ``Name``."""
        url = self.lightgg_url
        return f"[{self.name}]({url})" if url else self.name

    @classmethod
    def from_item(cls, item: "api.DestinyItem") -> "WeaponRef":
        return cls(name=item.name, hash=item.hash, emoji_name=item.expected_emoji_name)

    def to_dict(self) -> dict[str, t.Any]:
        return {"name": self.name, "hash": self.hash, "emoji_name": self.emoji_name}

    @classmethod
    def from_dict(cls, d: t.Mapping[str, t.Any]) -> "WeaponRef":
        return cls(name=d["name"], hash=d.get("hash"), emoji_name=d.get("emoji_name"))


# ---------------------------------------------------------------------------
# Components V2 renderer
# ---------------------------------------------------------------------------


def build_cv2(body: str, image_url: str | None) -> HMessage:
    """Wrap an already-emoji-substituted body + optional image in a CV2 HMessage."""
    container = h.impl.ContainerComponentBuilder(accent_color=cfg.embed_default_color)
    container.add_text_display(body)
    if image_url:
        gallery = h.impl.MediaGalleryComponentBuilder()
        gallery.add_media_gallery_item(image_url)
        container.add_component(gallery)
    container.add_separator(divider=True)
    container.add_text_display(FOOTER)
    return HMessage(components=[container])


# ---------------------------------------------------------------------------
# Rich HTML preview (web form)
# ---------------------------------------------------------------------------
#
# ``render_post_html`` renders the EXACT markdown subset the producers' ``build_body``
# emits into a small, safe HTML fragment for the web form's live preview: every text
# leaf is escaped, masked-link URLs are http(s)-validated, ``:emoji:`` tokens become
# <img> from the guild emoji dict (unknown names fall back to escaped text), and ONLY
# the whitelisted tags (strong / em / span / a / img) are emitted. The <pre> preview
# keeps newlines.

#: One inline-markdown token. Ordered so ``***`` beats ``**`` beats ``*`` and the
#: ``<t:…>`` timestamp beats the emoji rule (both can start with ``<``). The emoji arm
#: reuses ``re_user_side_emoji`` verbatim; its inner capture groups are ignored — the
#: matched span is re-substituted through the emoji substituter (which uses that regex).
_INLINE_MD = re.compile(
    r"(?P<bi>\*\*\*(?P<bi_inner>.+?)\*\*\*)"
    r"|(?P<b>\*\*(?P<b_inner>.+?)\*\*)"
    r"|(?P<i>\*(?P<i_inner>.+?)\*)"
    r"|(?P<link>\[(?P<label>[^\]]+)\]\((?P<url>[^)\s]+)\))"
    r"|(?P<ts><t:(?P<tsval>\d+):[A-Za-z]>)"
    r"|(?P<emoji>" + re_user_side_emoji.pattern + r")"
)


def _format_reset_ts(unix: int) -> str:
    """Render a ``<t:UNIX:f>`` instant as Discord's ``:f`` long-date short-time, in UTC.

    Discord shows ``:f`` in the *viewer's* local zone; the preview can't know that, so
    it renders in UTC with an explicit ``(UTC)`` note (e.g. "Jul 14, 2026 5:00 PM").
    """
    d = dt.datetime.fromtimestamp(unix, tz=dt.UTC)
    hour12 = d.hour % 12 or 12
    ampm = "AM" if d.hour < 12 else "PM"
    return f"{d.strftime('%b')} {d.day}, {d.year} {hour12}:{d.minute:02d} {ampm} (UTC)"


def _html_emoji_substituter(
    emoji_dict: dict[str, h.Emoji],
) -> t.Callable[[t.Any], str]:
    """An ``re_user_side_emoji`` substituter emitting <img> (modeled on the CV2 one).

    Emits ``<img class="emoji" src="{emoji.url}" alt=":name:">`` for a known guild
    emoji, else the escaped ``:name:`` text. Every attribute value is escaped.
    """

    def func(match: t.Any) -> str:
        name = str(match.group(2))
        emoji = emoji_dict.get(name) or emoji_dict.get(name.lower())
        if emoji is None:
            return html.escape(match.group(0))
        url = html.escape(str(getattr(emoji, "url", "")), quote=True)
        alt = html.escape(name, quote=True)
        return f'<img class="emoji" src="{url}" alt=":{alt}:">'

    return func


def _render_inline(text: str, emoji_sub: t.Callable[[t.Any], str]) -> str:
    """Render one line's inline markdown to safe HTML (escaping every text leaf)."""
    out: list[str] = []
    pos = 0
    for m in _INLINE_MD.finditer(text):
        if m.start() > pos:
            out.append(html.escape(text[pos : m.start()]))
        if m.group("bi") is not None:
            out.append(f"<strong><em>{html.escape(m.group('bi_inner'))}</em></strong>")
        elif m.group("b") is not None:
            out.append(f"<strong>{html.escape(m.group('b_inner'))}</strong>")
        elif m.group("i") is not None:
            out.append(f"<em>{html.escape(m.group('i_inner'))}</em>")
        elif m.group("link") is not None:
            url = m.group("url")
            if url.startswith(("http://", "https://")):
                href = html.escape(url, quote=True)
                # The label may itself carry markdown (e.g. "[**View…**](url)").
                label = _render_inline(m.group("label"), emoji_sub)
                out.append(f'<a href="{href}">{label}</a>')
            else:  # non-http(s): not a real link — render the raw text, escaped.
                out.append(html.escape(m.group("link")))
        elif m.group("ts") is not None:
            out.append(html.escape(_format_reset_ts(int(m.group("tsval")))))
        else:  # emoji
            out.append(re_user_side_emoji.sub(emoji_sub, m.group("emoji")))
        pos = m.end()
    if pos < len(text):
        out.append(html.escape(text[pos:]))
    return "".join(out)


def _render_line(line: str, emoji_sub: t.Callable[[t.Any], str]) -> str:
    """Render one body line, handling the heading, small-text and bullet prefixes.

    ``### `` (H3) and ``- `` (bullet) render as spans (never ``<ul>``/``<li>``), so the
    emitted tags stay within the ``{span, strong, em, a, img}`` whitelist the preview is
    trusted against. ``### `` is tested before ``# `` and ``-# `` before ``- `` so the
    longer prefix wins; the bullet marker is supplied by CSS ``.md-bullet::before``.
    """
    if line.startswith("### "):
        return f'<span class="md-h3">{_render_inline(line[4:], emoji_sub)}</span>'
    if line.startswith("# "):
        return f'<span class="md-h1">{_render_inline(line[2:], emoji_sub)}</span>'
    if line.startswith("-# "):
        return f'<span class="md-small">{_render_inline(line[3:], emoji_sub)}</span>'
    if line.startswith("- "):
        return f'<span class="md-bullet">{_render_inline(line[2:], emoji_sub)}</span>'
    return _render_inline(line, emoji_sub)


def render_post_html(
    body: str, emoji_dict: dict[str, h.Emoji], image_url: str | None = None
) -> str:
    """Render a ``build_body`` string (plus the ``-#`` FOOTER) to safe preview HTML.

    Mirrors what Discord renders for the published post: the same markdown subset,
    custom emoji as images, and the small-text footer ``build_cv2`` appends. Newlines
    are preserved for the <pre> preview. Only whitelisted tags (strong / em / span / a /
    img) are emitted; every text leaf is escaped and every URL is http(s)-validated, so
    it is safe to drop into the form's ``innerHTML`` sink despite the owner-authored
    input.
    """
    emoji_sub = _html_emoji_substituter(emoji_dict)
    lines = [_render_line(line, emoji_sub) for line in body.split("\n")]
    # Image sits below the body and above the footer — mirroring build_cv2's media
    # gallery placement — so the preview shows it exactly where the post does.
    if image_url and image_url.startswith(("http://", "https://")):
        src = html.escape(image_url, quote=True)
        lines += ["", f'<img class="post-image" src="{src}" alt="post image">']
    # Append the footer build_cv2 adds to the real post, for parity with the publish.
    lines += ["", _render_line(FOOTER, emoji_sub)]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Draft metadata (post message id, publish status, "needs attention" flags)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class DraftMeta:
    #: Id of the single in-channel post in the followable; 0 = not posted.
    message_id: int = 0
    #: Wall-clock reset boundary (``current_reset_ts()`` at post time) of the period the
    #: tracked ``message_id`` belongs to. Stamped from the clock, NOT the draft's
    #: (user-overridable) ``reset_ts``, so it always names the real period. Lets the
    #: form tell if the tracked post is *this* period's (see :meth:`is_current`). A
    #: legacy doc predating this field carries 0.
    reset_ts: int = 0
    #: Whether that post has been crossposted (broadcast to followers via beacon).
    crossposted: bool = False
    #: "draft" (no post) | "posted" (uncrossposted) | "published" (crossposted).
    status: str = "draft"
    last_edited_by: int = 0
    last_edited_ts: int = 0
    needs_attention: list[str] = dataclasses.field(default_factory=list)

    def is_current(self, reset_ts: int) -> bool:
        """Whether the tracked post should be managed as reset period ``reset_ts``'s.

        Drives the form's Edit/Delete-vs-Create split. True when a post exists and its
        stamped period matches ``reset_ts`` — OR the stamp is 0, i.e. a legacy doc from
        before per-period tracking: its live post is treated as current so it stays
        editable/deletable instead of being duplicated by a Create. A post whose stamp
        names a *different* (past-or-future) period is not current — the form starts a
        fresh draft for ``reset_ts`` and offers Create. NB for producers whose post is
        optional (e.g. Trials): a ``False`` here is a normal "no post this period"
        state, not an error.
        """
        return self.message_id != 0 and self.reset_ts in (0, reset_ts)

    def to_dict(self) -> dict[str, t.Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: t.Mapping[str, t.Any] | None) -> "DraftMeta":
        if not d:
            return cls()
        status = d.get("status", "draft")
        # Back-compat: pre-lifecycle docs stored ``published_message_id`` (and no
        # ``crossposted``). Read the old key into ``message_id`` and default
        # ``crossposted`` from the legacy "published" status. ``reset_ts`` predates the
        # per-period tracking, so old docs default it to 0 — which ``is_current`` treats
        # as the current period so a pre-existing live post stays manageable on deploy.
        message_id = int(d.get("message_id", d.get("published_message_id", 0)) or 0)
        return cls(
            message_id=message_id,
            reset_ts=int(d.get("reset_ts", 0) or 0),
            crossposted=bool(d.get("crossposted", status == "published")),
            status=status,
            last_edited_by=int(d.get("last_edited_by", 0)),
            last_edited_ts=int(d.get("last_edited_ts", 0)),
            needs_attention=list(d.get("needs_attention") or []),
        )


# ---------------------------------------------------------------------------
# Publish-time error messaging
# ---------------------------------------------------------------------------


def _discord_error_note(exc: Exception) -> str:
    """A short, user-facing reason for a failed in-channel post/edit/crosspost.

    Discord rejects proxied/temporary image URLs (e.g. ``images-ext-*.discordapp.net``
    or ``media.discordapp.net/external/…`` links copied from an embed/tweet) with an
    "Invalid resource" 401 — the most common cause of a failed post here. Surface a
    concrete hint for that; otherwise pass the trimmed Discord message through.
    """
    msg = str(getattr(exc, "message", "") or exc)
    if "Invalid resource" in msg or "discordapp.net/external/" in msg:
        return (
            "Discord rejected the image URL — it looks like a Discord/social-media "
            "proxy link. Paste the original direct image URL instead (e.g. the "
            "https://pbs.twimg.com/… link, or a Discord attachment URL)."
        )
    return f"Discord rejected the post: {msg[:200]}"


# ---------------------------------------------------------------------------
# Producer spec + publishing
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class HybridPostSpec:
    """The producer-specific hooks the generic publish/route code needs.

    One instance per producer (weekly_reset, trials). Context objects (the producer's
    ``*Context`` dataclass) are opaque to this module — they are only passed back to
    ``render``/``validate``, so the callables are typed with ``...`` parameters rather
    than a shared context type. ``render`` in particular should late-resolve the
    producer's ``format_*`` (so a monkeypatched renderer is honoured) — see
    ``weekly_reset``'s spec construction.
    """

    #: Key into ``cfg.followables`` for the channel this post publishes to.
    followable_key: str
    #: Human name of the post for the Create/Edit 409 messages (e.g. "Trials post").
    post_noun: str
    #: ``() -> int`` — the current reset-period boundary used for
    #: ``DraftMeta.is_current`` (the create-vs-edit split). A producer-supplied hook
    #: (usually a late-binding wrapper over its module ``current_reset_ts``) so a test
    #: that monkeypatches the producer's ``current_reset_ts`` steers the route code's
    #: notion of "now".
    current_reset_ts: t.Callable[..., int]
    #: async ``(ctx, bot) -> HMessage`` — render the context to the CV2 message.
    render: t.Callable[..., t.Awaitable[HMessage]]
    #: ``(ctx) -> list[str]`` — publish-blocking problems (empty = ok).
    validate: t.Callable[..., list[str]]
    #: ``(ctx) -> str`` — the post body markdown (for the live preview).
    build_body: t.Callable[..., str]
    #: async ``() -> ctx | None`` — load the persisted draft (None = none saved).
    load_draft: t.Callable[..., t.Awaitable[t.Any]]
    #: async ``(ctx) -> None`` — persist the draft.
    save_draft: t.Callable[..., t.Awaitable[None]]
    #: async ``() -> ctx`` — build a fresh seeded draft (form-load fallback).
    build_context: t.Callable[..., t.Awaitable[t.Any]]
    #: async ``(payload) -> ctx`` — server-side context from the form JSON.
    context_from_payload: t.Callable[..., t.Awaitable[t.Any]]
    #: async ``() -> DraftMeta`` — load the draft metadata row.
    load_meta: t.Callable[..., t.Awaitable[DraftMeta]]
    #: async ``(meta) -> None`` — persist the draft metadata row.
    save_meta: t.Callable[..., t.Awaitable[None]]
    #: async ``(draft, meta) -> dict`` — the page bootstrap JSON for the form.
    build_bootstrap: t.Callable[..., t.Awaitable[dict[str, t.Any]]]
    #: async ``(payload, ctx) -> None`` — persist the carried-over default image if the
    #: form's "use as default" box is ticked (else a no-op).
    persist_default_image: t.Callable[..., t.Awaitable[None]]
    #: async ``() -> bool | None`` — is the reset-day autopost enabled?
    get_autopost: t.Callable[..., t.Awaitable[bool | None]]
    #: async ``(bool) -> None`` — set the reset-day autopost toggle.
    set_autopost: t.Callable[..., t.Awaitable[None]]
    #: The producer's web-form HTML template (bootstrap placeholder substituted in).
    form_html_path: Path
    #: Serialises read-modify-write of the shared draft doc (single bot process).
    draft_lock: asyncio.Lock
    #: Optional async ``(ctx) -> None`` fired ONCE when a post transitions to
    #: crossposted (published to followers) — i.e. it actually went live this period.
    #: Producers use it for "on publish" side effects (Trials advances its loot-set
    #: rotation here); NOT fired for uncrossposted posts/edits or the seeding cron, so a
    #: draft that is never published (or is deleted) has no effect.
    on_published: t.Callable[..., t.Awaitable[None]] | None = None

    @property
    def channel_id(self) -> int:
        return cfg.followables[self.followable_key]


# ---------------------------------------------------------------------------
# Preview emoji cache (shared by every producer's /preview route)
# ---------------------------------------------------------------------------

#: Short-lived cache of the guild emoji dict used to render the rich preview. Each form
#: POSTs on every ~400 ms keystroke, so a REST fetch per request would hammer Discord —
#: cache the dict for a few minutes instead. The dict is the same Kyber guild for every
#: producer, so one process-wide cache serves them all.
_EMOJI_CACHE_TTL = dt.timedelta(minutes=5)
_emoji_cache: dict[str, h.Emoji] | None = None
_emoji_cache_at: dt.datetime | None = None


async def preview_emoji_dict(bot: CachedFetchBot | None) -> dict[str, h.Emoji]:
    """The Kyber guild emoji dict for the preview, cached with a short TTL.

    Returns an empty dict (no emoji substitution) when the bot isn't up yet or the fetch
    fails, so the preview degrades to escaped ``:name:`` text rather than erroring.
    """
    global _emoji_cache, _emoji_cache_at
    if bot is None:
        return {}
    now = dt.datetime.now(tz=dt.UTC)
    if (
        _emoji_cache is not None
        and _emoji_cache_at is not None
        and now - _emoji_cache_at < _EMOJI_CACHE_TTL
    ):
        return _emoji_cache
    try:
        _emoji_cache = await fetch_emoji_dict(bot)
        _emoji_cache_at = now
    except Exception:
        logger.warning("hybrid_post_core: preview emoji fetch failed", exc_info=True)
        return _emoji_cache or {}
    return _emoji_cache


# ---------------------------------------------------------------------------
# Web-form routes (auth is enforced centrally by the web_auth middleware)
# ---------------------------------------------------------------------------
#
# One set of handler bodies serves every producer; the producer-specific bits (context
# model, bootstrap payload, option pools) come through ``spec``. Each producer keeps six
# thin ``_handle_*`` wrappers that pass its ``spec`` and live ``_bot`` in, so the tests
# that call the wrappers and monkeypatch the module ``_bot`` keep working unchanged.

_STARTING_MSG = "Bot is still starting — try again in a moment."


def _bot_starting() -> aiohttp.web.Response:
    return aiohttp.web.json_response({"error": _STARTING_MSG}, status=503)


async def form_get(
    spec: HybridPostSpec, request: aiohttp.web.Request, bot: CachedFetchBot | None
) -> aiohttp.web.Response:
    # Auth is enforced by the web_auth middleware; this just renders the form.
    meta = await spec.load_meta()
    # When a post exists for this period, open the form on the saved draft that tracks
    # it (so you edit what's live); else start a fresh draft for the current period.
    # Keyed off the post's tracked period (is_current), NOT the draft's own reset_ts —
    # a user-overridable display field that must not decide staleness. A producer whose
    # post is optional (Trials) simply reports post_this_period False when none exists.
    post_this_period = meta.is_current(spec.current_reset_ts())
    draft = (await spec.load_draft() if post_this_period else None) or (
        await spec.build_context()
    )
    bootstrap = await spec.build_bootstrap(draft, meta)
    # Escape "<" so a "</script>" in the data can't break out of the inline <script>.
    bootstrap_js = json.dumps(bootstrap).replace("<", "\\u003c")
    page = spec.form_html_path.read_text(encoding="utf-8").replace(
        "/*__BOOTSTRAP__*/ null", bootstrap_js
    )
    return aiohttp.web.Response(text=page, content_type="text/html")


async def post_action(
    spec: HybridPostSpec,
    request: aiohttp.web.Request,
    bot: CachedFetchBot | None,
    *,
    create: bool,
) -> aiohttp.web.Response:
    """Shared backend for the Create/Edit (± publish) form actions.

    ``create=True`` sends a brand-new in-channel post for the current period (409 if one
    already exists — the form hides the button, this enforces it server-side, and
    forgets any stale prior-period id so we never edit a past post). ``create=False``
    edits the existing current-period post in place (409 if there is none).

    ``payload["publish"]`` selects the crosspost behaviour: publishing validates
    strictly and broadcasts to followers (blocking ``problems`` on failure); the plain
    post/edit is lenient (advisory ``warnings``, the draft is kept even if Discord
    rejects the post) — but a failed send/edit is a blocking ``problem`` so the form
    can't show a false "done". Both persist the draft so the saved copy tracks it.
    """
    if bot is None:
        return _bot_starting()
    try:
        payload = await request.json()
    except Exception:
        return aiohttp.web.json_response({"error": "Malformed body."}, status=400)
    publish = bool(payload.get("publish"))
    ctx = await spec.context_from_payload(payload)

    async with spec.draft_lock:
        meta = await spec.load_meta()
        post_this_period = meta.is_current(spec.current_reset_ts())
        if create and post_this_period:
            return aiohttp.web.json_response(
                {
                    "error": f"A {spec.post_noun} already exists for this period — "
                    "edit or delete it instead."
                },
                status=409,
            )
        if not create and not post_this_period:
            return aiohttp.web.json_response(
                {
                    "error": f"No {spec.post_noun} exists for this period yet — "
                    "create one first."
                },
                status=409,
            )
        # Create drops the message-tracking fields so a fresh message is sent (and any
        # stale prior-period id is forgotten), while keeping editorial metadata like the
        # last editor; edit keeps the current meta so its message is updated in place.
        if create:
            meta.message_id = 0
            meta.reset_ts = 0
            meta.crossposted = False
            meta.status = "draft"
        meta.last_edited_ts = int(dt.datetime.now(tz=dt.UTC).timestamp())
        await spec.save_draft(ctx)

        note: str | None = None
        was_crossposted = meta.crossposted
        if publish:
            # Publishing validates strictly and crossposts; problems block the send.
            try:
                meta, note = await publish_draft(spec, bot, ctx, meta)
            except ValueError as exc:
                return aiohttp.web.json_response(
                    {"problems": str(exc).split("; ")}, status=422
                )
            except Exception as exc:  # Discord rejected the post/crosspost (bad image…)
                logger.warning("%s: publish failed", spec.followable_key, exc_info=True)
                # ``publish_draft`` may have already SENT the message (stamping
                # ``meta.message_id``) and only failed on the crosspost — persist the
                # meta so that live post isn't orphaned (a next Create would duplicate
                # it). Safe when nothing was sent: message_id is still 0.
                await spec.save_meta(meta)
                return aiohttp.web.json_response(
                    {"problems": [_discord_error_note(exc)]}, status=502
                )
            warnings: list[str] = []
        else:
            # Post/edit the uncrossposted message. Content problems (validate) are
            # non-blocking advisory warnings, but if the send/edit itself fails (e.g. a
            # bad image URL) the message did NOT change — report that as a blocking
            # problem so the form can't show a false "done ✓". The draft stays saved, so
            # the user fixes and retries without losing their in-page edits.
            warnings = spec.validate(ctx)
            try:
                meta = await post_or_edit_unpublished(spec, bot, ctx, meta)
            except Exception as exc:
                logger.warning(
                    "%s: in-channel post update failed",
                    spec.followable_key,
                    exc_info=True,
                )
                return aiohttp.web.json_response(
                    {"problems": [_discord_error_note(exc)]}, status=502
                )
        await spec.save_meta(meta)
        # Optionally persist this period's image as the carried-over default for future
        # drafts. An empty image URL with the box ticked clears the default.
        await spec.persist_default_image(payload, ctx)
        # Fire the "on publish" hook ONCE, only when this action actually took the post
        # live (uncrossposted -> crossposted). Uncrossposted posts/edits and the seeding
        # cron never reach here, so a draft that's never published has no side effect.
        if not was_crossposted and meta.crossposted and spec.on_published is not None:
            await spec.on_published(ctx)
    logger.info(
        "%s: %s via web form (publish=%s)",
        spec.followable_key,
        "created" if create else "edited",
        publish,
    )
    return aiohttp.web.json_response(
        {
            "ok": True,
            "note": note,
            "warnings": warnings,
            "post_this_period": meta.is_current(spec.current_reset_ts()),
            "crossposted": meta.crossposted,
        }
    )


async def preview(
    spec: HybridPostSpec, request: aiohttp.web.Request, bot: CachedFetchBot | None
) -> aiohttp.web.Response:
    try:
        payload = await request.json()
    except Exception:
        return aiohttp.web.Response(status=400, text="Malformed body.")
    ctx = await spec.context_from_payload(payload)
    # Rich preview: render the post's markdown subset to safe HTML (emoji as <img>,
    # bold/italic/links/dates), matching what Discord shows for the published post. The
    # renderer escapes every text leaf and validates URLs, so this is safe for the
    # client's innerHTML sink. Emoji come from the short-TTL guild-emoji cache.
    emoji_dict = await preview_emoji_dict(bot)
    return aiohttp.web.Response(
        text=render_post_html(spec.build_body(ctx), emoji_dict, ctx.image_url),
        content_type="text/html",
    )


async def delete(
    spec: HybridPostSpec, request: aiohttp.web.Request, bot: CachedFetchBot | None
) -> aiohttp.web.Response:
    if bot is None:
        return _bot_starting()
    # Delete the in-channel post and reset the draft to unposted, under the same lock
    # the create/edit paths use. Deleting a crossposted message propagates the deletion
    # to following channels (and beacon mirrors the delete), so the post is removed
    # everywhere; the persisted draft data is kept so a later Create re-posts it.
    async with spec.draft_lock:
        meta = await spec.load_meta()
        if meta.message_id:
            channel_id = spec.channel_id
            try:
                await bot.rest.delete_message(channel_id, meta.message_id)
            except h.NotFoundError:
                pass  # already gone — fall through and reset the meta
            except Exception as exc:  # keep the meta so the post isn't orphaned
                logger.warning("%s: delete failed", spec.followable_key, exc_info=True)
                return aiohttp.web.json_response(
                    {"ok": False, "error": _discord_error_note(exc)}, status=502
                )
            meta.message_id = 0
            meta.reset_ts = 0
            meta.crossposted = False
            meta.status = "draft"
            await spec.save_meta(meta)
    return aiohttp.web.json_response({"ok": True})


async def auto(
    spec: HybridPostSpec, request: aiohttp.web.Request, bot: CachedFetchBot | None
) -> aiohttp.web.Response:
    try:
        payload = await request.json()
    except Exception:
        return aiohttp.web.json_response({"error": "Malformed body."}, status=400)
    await spec.set_autopost(bool(payload.get("enabled", False)))
    state = bool(await spec.get_autopost())
    return aiohttp.web.json_response({"enabled": state})


async def _send_new_post(
    spec: HybridPostSpec, bot: CachedFetchBot, hmessage: HMessage, meta: DraftMeta
) -> None:
    """Send a fresh uncrossposted post and stamp its id + reset period onto ``meta``.

    Shared by the two "first post of the period" paths (:func:`post_or_edit_unpublished`
    and :func:`publish_draft`'s fallback). ``reset_ts`` is stamped from the producer's
    reset clock (``spec.current_reset_ts()``), NOT ``ctx.reset_ts`` — the draft boundary
    is a display field the user can override, but the tracked period must name the real
    reset period so :meth:`DraftMeta.is_current` stays correct.
    """
    posted = await utils.send_message(bot, hmessage, spec.channel_id, crosspost=False)
    meta.message_id = posted.id
    meta.reset_ts = spec.current_reset_ts()


async def post_or_edit_unpublished(
    spec: HybridPostSpec, bot: CachedFetchBot, ctx: t.Any, meta: DraftMeta
) -> DraftMeta:
    """Create-or-update the *uncrossposted* in-channel post for the current draft.

    The first call (``message_id == 0``) sends the assembled post to the followable
    WITHOUT crossposting, so the team can see and iterate it in Discord before it is
    broadcast. Later calls edit that message in place — this works whether the post is
    still uncrossposted or already published (an edit to a crossposted message
    re-mirrors via beacon). Returns the updated ``meta`` (caller persists); never
    crossposts.
    """
    hmessage = await spec.render(ctx, bot)
    channel_id = spec.channel_id
    if meta.message_id == 0:
        await _send_new_post(spec, bot, hmessage, meta)
        meta.status = "posted"
    else:
        await bot.rest.edit_message(
            channel_id, meta.message_id, components=hmessage.components
        )
    return meta


async def publish_draft(
    spec: HybridPostSpec, bot: CachedFetchBot, ctx: t.Any, meta: DraftMeta
) -> tuple[DraftMeta, str]:
    """Publish (crosspost) the existing in-channel post to the followable's channel.

    Publishing means *crossposting the post the team has iterated on* (created by
    :func:`post_or_edit_unpublished`), not sending a fresh message: the draft is first
    synced onto that message in place, then the message is crossposted so beacon mirrors
    it to every follower. Crossposting is idempotent — a re-publish (or any later
    save-driven edit) just re-mirrors the edit, no duplicate. Falls back to
    post-then-crosspost when nothing has been posted yet (``message_id == 0``). Returns
    the updated ``meta`` and a short note; raises ``ValueError`` (the joined
    ``spec.validate`` problems) instead of publishing an invalid post.
    """
    problems = spec.validate(ctx)
    if problems:
        raise ValueError("; ".join(problems))
    hmessage = await spec.render(ctx, bot)
    channel_id = spec.channel_id
    was_crossposted = meta.crossposted
    if meta.message_id:
        # Sync the in-channel post to the current draft before broadcasting it.
        await bot.rest.edit_message(
            channel_id, meta.message_id, components=hmessage.components
        )
    else:
        # No post yet (e.g. publish before any save): post it first, uncrossposted.
        await _send_new_post(spec, bot, hmessage, meta)
    await utils.crosspost_message_with_retries(bot, channel_id, meta.message_id)
    meta.crossposted = True
    meta.status = "published"
    note = (
        "✏️ Updated the published post — beacon re-mirrors the edit."
        if was_crossposted
        else "✅ Published and crossposted — beacon will mirror it out."
    )
    return meta, note


# ---------------------------------------------------------------------------
# Manifest weapon pool + resolver (shared by every producer's reward pickers)
# ---------------------------------------------------------------------------

#: One weapon/armour row: (name, hash, itemTypeDisplayName, itemType, rarity).
WeaponItem = tuple[str, int, str, int, str]


async def iter_weapon_items(cursor: t.Any) -> list[WeaponItem]:
    """Read the manifest's named, non-dummy weapons/armour via ``cursor``, deduped.

    Runs the ``DestinyInventoryItemDefinition`` query on the caller-owned sqlite cursor
    (so a producer can share one manifest connection across several reads) and returns
    one row per (name, type), newest hash winning — the pool the reward autocomplete and
    :func:`resolve_weapon` search. Whites/greens and redacted/dummy items are dropped.
    """
    item_by_key: dict[tuple[str, str], WeaponItem] = {}
    await cursor.execute("SELECT json FROM DestinyInventoryItemDefinition")
    for (row,) in await cursor.fetchall():
        defn = json.loads(row)
        item_type = defn.get("itemType")
        if item_type not in (2, 3) or defn.get("redacted"):
            continue
        rarity = (defn.get("inventory") or {}).get("tierTypeName", "")
        if rarity in ("", "Common", "Basic"):  # drop dummies/whites/greens
            continue
        name = (defn.get("displayProperties") or {}).get("name")
        if not name:
            continue
        type_name = defn.get("itemTypeDisplayName", "")
        hash_ = int(defn["hash"])
        key = (name.lower(), type_name.lower())
        existing = item_by_key.get(key)
        if existing is None or hash_ > existing[1]:  # keep the newest hash
            item_by_key[key] = (name, hash_, type_name, item_type, rarity)
    return sorted(item_by_key.values(), key=lambda e: e[0].lower())


#: Process-wide cache of the manifest weapon/armour pool + its build lock. Every
#: producer's reward pickers search the SAME pool, so the ~4166-row
#: DestinyInventoryItemDefinition scan + JSON decode runs once and is held in a single
#: list, not one copy per producer.
_weapon_pool: list[WeaponItem] | None = None
_weapon_pool_lock = asyncio.Lock()


async def get_weapon_pool() -> list[WeaponItem]:
    """Build (once) and cache the manifest weapon/armour pool, shared process-wide.

    Opens its own short-lived manifest connection and runs :func:`iter_weapon_items`;
    the result is cached so subsequent callers (every producer + its prewarm) reuse it
    rather than re-scanning the item table. On any failure returns ``[]`` **without
    caching**: the caller degrades to a manifest-less form and a later call retries, so
    a transient manifest error doesn't permanently disable the reward pickers.
    """
    global _weapon_pool
    if _weapon_pool is not None:
        return _weapon_pool
    async with _weapon_pool_lock:
        if _weapon_pool is None:
            try:
                path = await api._get_latest_manifest(schemas.BungieCredentials.api_key)
                async with aiosqlite.connect(path) as con:
                    cur = await con.cursor()
                    _weapon_pool = await iter_weapon_items(cur)
            except Exception:
                logger.warning("manifest weapon-pool build failed", exc_info=True)
                return []
        return _weapon_pool


def resolve_weapon(value: str, items: t.Sequence[WeaponItem]) -> WeaponRef | None:
    """A hash (picked from autocomplete) -> full WeaponRef; else a plain typed name.

    ``value`` is either a manifest hash (an autocomplete pick) resolved against
    ``items`` to a light.gg-linked, emoji-typed :class:`WeaponRef`, a case-insensitive
    name match, or — failing both — a hash-less plain-text ``WeaponRef`` for a
    free-typed name. An empty ``value`` clears the slot (``None``).
    """
    value = value.strip()
    if not value:
        return None
    if value.isdigit():
        wanted = int(value)
        for name, hash_, type_name, _item_type, _rarity in items:
            if hash_ == wanted:
                return WeaponRef(name, hash_, api.likely_emoji_name(type_name))
    for name, hash_, type_name, _item_type, _rarity in items:
        if name.lower() == value.lower():
            return WeaponRef(name, hash_, api.likely_emoji_name(type_name))
    return WeaponRef(name=value)
