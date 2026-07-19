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

"""Iron Banner — domain model + post layout (shared, no Discord/manifest access).

Iron Banner runs one week roughly every 4 weeks (on the weeks Trials is *not* live). Its
post is fully automatic — the ``iron_banner`` anchor producer posts it once when an
event window opens — so there is no cursor and no web form: the schedule is
**date-anchored**.

This module is the pure half (mirroring :mod:`dd.common.lost_sector`'s split): it parses
the editor-managed ``iron_banner`` :class:`~dd.common.schemas.RotationData` doc into
:class:`Event` objects, answers "which event is live now / next", and lays out the post
markdown (:func:`build_body`). Resolving weapon names to light.gg links + weapon-type
emoji needs the manifest, so the producer does that (anchor-side) and passes the
finished ``pool_lines`` in — keeping this module free of Discord/manifest imports so
both the producer and the rotation-editor preview share one layout.
"""

import dataclasses
import datetime as dt
import logging
import typing as t

from ..common import rotation_schema, schemas

logger = logging.getLogger(__name__)

_IRON_BANNER = rotation_schema.IRON_BANNER_SLUG

#: The full guide the post highlights and links out to (title link + footer button).
GUIDE_URL = "https://kyberscorner.com/destiny2/iron-banner/"
#: Post-specific footer guide button(s); the shared Support button
#: are appended by ``components.footer_button_specs``. Shared by post + preview.
GUIDES: tuple[tuple[str, str], ...] = (("Iron Banner Guide", GUIDE_URL),)

#: Iron Banner begins at the weekly Tuesday reset — 17:00 UTC.
_RESET_HOUR_UTC = 17
_WEEK = dt.timedelta(days=7)

# Last-known-good rotation, served only if the DB store is unreachable so a transient DB
# blip never breaks the autopost (mirrors ``lost_sector._rotation_cache``).
_rotation_cache: "IronBannerRotation | None" = None


def _split_modes(modes: str | None) -> list[str]:
    """Split a ``"Control / Eruption"`` modes string into ``["Control", "Eruption"]``.

    Blank/absent falls back to the default modes, so an entry that omits ``modes`` still
    renders a Game Modes section.
    """
    text = (modes or "").strip() or rotation_schema.IRON_BANNER_DEFAULT_MODES
    return [part.strip() for part in text.split("/") if part.strip()]


def _start_ts(start: str) -> int:
    """Unix ts of an Iron Banner week's start — the ``YYYY-MM-DD`` date at 17:00 UTC."""
    day = dt.date.fromisoformat(start.strip())
    return int(
        dt.datetime(
            day.year, day.month, day.day, _RESET_HOUR_UTC, tzinfo=dt.UTC
        ).timestamp()
    )


@dataclasses.dataclass(frozen=True)
class Event:
    """One Iron Banner week: its window, active bonus pool, and game modes."""

    start_ts: int
    end_ts: int
    pool_name: str
    modes: list[str]
    #: The bonus focus pool's weapon names, as stored (may carry a ``" (Type)"`` suffix
    #: the producer strips before resolving to manifest items).
    pool_weapon_names: list[str]


class IronBannerRotation:
    """The parsed Iron Banner schedule + bonus focus pools, ordered by start date."""

    def __init__(self, events: list[Event]):
        self.events = sorted(events, key=lambda e: e.start_ts)

    @classmethod
    def from_json(cls, doc: t.Mapping[str, t.Any]) -> "IronBannerRotation":
        """Build from an ``iron_banner`` RotationData document (a hard gate).

        Raises :class:`ValueError` if a schedule entry names an undefined pool or a
        start date that doesn't parse — the same "structurally unusable" gate the
        editor's save/preview surface to the operator.
        """
        pools = {
            str(p.get("name", "")): [str(w) for w in p.get("weapons") or []]
            for p in doc.get("pools") or []
        }
        events: list[Event] = []
        for entry in doc.get("schedule") or []:
            pool_name = str(entry.get("pool", ""))
            if pool_name not in pools:
                raise ValueError(
                    f"schedule entry references undefined pool {pool_name!r}"
                )
            start = str(entry.get("start", ""))
            try:
                start_ts = _start_ts(start)
            except ValueError as exc:
                raise ValueError(f"invalid start date {start!r}: {exc}") from exc
            events.append(
                Event(
                    start_ts=start_ts,
                    end_ts=start_ts + int(_WEEK.total_seconds()),
                    pool_name=pool_name,
                    modes=_split_modes(entry.get("modes")),
                    pool_weapon_names=pools[pool_name],
                )
            )
        return cls(events)

    def active_event(self, now: dt.datetime | None = None) -> Event | None:
        """The event whose ``[start, end)`` window contains ``now`` (else ``None``)."""
        ts = int((now or dt.datetime.now(tz=dt.UTC)).timestamp())
        for event in self.events:
            if event.start_ts <= ts < event.end_ts:
                return event
        return None

    def current_or_next(self, now: dt.datetime | None = None) -> Event | None:
        """The live event, else the soonest upcoming one (for ``send``/``show``)."""
        ts = int((now or dt.datetime.now(tz=dt.UTC)).timestamp())
        active = self.active_event(now)
        if active is not None:
            return active
        upcoming = [e for e in self.events if e.start_ts >= ts]
        return upcoming[0] if upcoming else None


async def load_rotation() -> IronBannerRotation:
    """Load the Iron Banner rotation from the DB JSON store.

    Resolution order: the ``RotationData['iron_banner']`` document → (only for a *clean
    absent row*) the baked default doc, so the schedule works before anyone edits it →
    the last-known-good cache. The DB is consulted every call so an editor save takes
    effect immediately.

    A **clean absent row** (``get_data`` returns ``None`` with no error) is the intended
    first-run state, so the baked default seeds it. But a **DB read error** or a
    **malformed stored doc** must NOT silently fall back to the default — that would
    crosspost the seed schedule over the operator's real (but currently unreadable)
    intent. In those cases serve the last-known-good cache, and if there is none, raise
    so the producer skips the run rather than posting defaults (mirrors
    :func:`dd.common.lost_sector.load_rotation`).
    """
    global _rotation_cache
    try:
        doc = await schemas.RotationData.get_data(_IRON_BANNER)
        db_ok = True
    except Exception:
        logger.exception("Failed to read iron_banner rotation from the DB")
        doc, db_ok = None, False

    # Clean absent row → seed with the baked default (the intended pre-edit state).
    if db_ok and doc is None:
        doc = rotation_schema.iron_banner_default_doc()

    if doc is not None:
        try:
            rotation = IronBannerRotation.from_json(doc)
            _rotation_cache = rotation
            return rotation
        except Exception:
            logger.exception("Stored iron_banner rotation JSON is malformed")

    # DB unreachable, or the stored doc is malformed: serve the last-known-good cache;
    # else raise so the caller skips this run instead of posting the baked default.
    if _rotation_cache is not None:
        return _rotation_cache
    raise RuntimeError(
        "No usable iron_banner rotation (DB unreadable or stored doc malformed, and "
        "nothing cached)"
    )


def build_body(event: Event, pool_lines: list[str]) -> str:
    """The Iron Banner post body markdown, with raw ``:emoji:`` tokens un-substituted.

    ``pool_lines`` are the already-resolved bonus-pool lines (each e.g.
    ``":auto_rifle: [The Forward Path](https://light.gg/db/items/…)"``) — the producer
    builds them from the manifest so this stays render-agnostic and shared with the
    editor preview. Highlights only (dates, modes, bonus pool) + a link to the full
    guide; the guide covers everything else.
    """
    lines: list[str] = [
        f"# [Iron Banner]({GUIDE_URL})",
        "",
        f"<t:{event.start_ts}:D> – <t:{event.end_ts}:D>",
        f"Live until <t:{event.end_ts}:f>",
    ]

    if event.modes:
        lines += ["### Game Modes", ""]
        lines += [f"- {mode}" for mode in event.modes]

    pool = [line for line in pool_lines if line]
    if pool:
        lines += ["### Bonus Focus Pool", ""]
        # No "- " bullet: the leading weapon-type emoji is the marker (as in Trials).
        lines += pool

    # The guide + Support links live in the footer button row (added by the producer /
    # preview), not the markdown body — so nothing more is appended here.
    return "\n".join(lines)
