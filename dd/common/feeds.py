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

"""The feed catalog: which followables exist, and what each one is called.

A *followable* is a feed anchor posts into a Kyber announcement channel and beacon
reads back — paging it through a navigator command, repeating it, and mirroring it out
to follower guilds. This module is the single declaration of that set.

**Why this is code and not a table.** Enumeration has to be *total in every process*:
anchor's settings page must render all eleven feeds, and anchor has producer modules
for only eight of them. An import-time registry (``dd.anchor.autopost.register_feed``)
is the right answer for *wiring* — which producer coro builds which post — because
wiring is naturally partial per process. It is the wrong answer for enumeration,
because the three beacon-only feeds would simply be missing from the very process that
needs the list. A static catalog is total by construction. It is also never refreshed:
this set changes only when someone writes a new extension module and redeploys, which
restarts the process and rebuilds it from scratch.

**What belongs here.** A fact earns a field if both processes need it, or if it is
currently written more than once. Everything else stays with its single consumer:

- cron schedules and producer/announcer coros — anchor wiring, one copy each, and
  holding the coros would force ``dd.common`` to import ``dd.anchor``;
- navigator wiring (period, reference date, history length, the ``NavPages``
  subclasses) — beacon wiring, heterogeneous, one copy each;
- the settings page's sub-toggle and image-URL rows — page copy for a few feeds.

``channel_key`` and ``has_toggle`` are properties rather than fields for the same
reason: derivable facts are not data. In particular ``channel_key`` is now the ONE
place the ``"<slug>_channel"`` convention lives — it used to exist only as a pattern
visible by eye across twelve hand-written pairs in
``dd.common.settings.FOLLOWABLE_SLUGS``.

This module imports nothing from ``dd`` (``dd.common.settings`` imports *it*), so there
is no cycle and no import-order constraint.
"""

import dataclasses
import enum


class FeedKind(enum.Enum):
    """Whether a feed is produced on a schedule — which decides its settings group.

    Only :attr:`ANCHOR_CRON` feeds have an ``enabled`` toggle, because only they have a
    scheduled producer to switch off; see :attr:`Followable.has_toggle`. The toggles are
    anchor's alone — beacon reads no setting to decide whether to mirror, and mirroring
    is unconditional (see ``dd.beacon.extensions.mirror``).

    This started as three members, splitting :attr:`UNSCHEDULED` into "anchor produces
    it from a web form" and "content arrives some other way". Nothing ever branched on
    that difference — ``has_toggle`` collapsed both to False — so it was a fact with no
    consumer in the one module whose purpose is removing facts that drift.

    Something branches on it now: anchor's feeds page groups its eleven feeds three
    ways, and "written by you" is exactly the web-form pair. The member still does not
    come back, because the fact is not one this module can hold honestly. A feed is
    written by a human because **anchor has a form wired to it** — a ``HybridPostSpec``,
    at import time in a process beacon does not share. Restating that here would put a
    copy of anchor's wiring in the catalog, where nothing can check it and where beacon
    would carry an assertion it has no way to test. The page derives it from
    ``dd.anchor.hybrid_post_core.registered_specs()`` instead, which is the same
    enumeration-vs-wiring split this module's own docstring draws.
    """

    #: anchor produces on a schedule (``@aiocron.crontab`` in the producer module).
    #: The only kind with a produce toggle: a toggle switches a schedule off.
    ANCHOR_CRON = enum.auto()
    #: everything else — a web form a human presses publish on (``HybridPostSpec``), or
    #: content that arrives in the channel by other means (a human, another bot). Either
    #: way there is no schedule to gate, and beacon follows and mirrors it the same.
    UNSCHEDULED = enum.auto()


@dataclasses.dataclass(frozen=True)
class Followable:
    """One feed's identity — everything about it that is not wiring."""

    #: The canonical id, and the join key across every store and surface: the
    #: ``AutoPostSettings`` toggle row name, ``dd.anchor.autopost.Feed.name``,
    #: ``HybridPostSpec.followable_key``, the ``/feed/{name}/…`` URL segment, and
    #: ``AutopostDailyStat.feed``.
    slug: str
    kind: FeedKind
    #: THE display name. Before this catalog the same feed was named four or five times
    #: — ``Xûr``/``Xur``, ``Ada-1``/``Ada``, ``Lost Sector``/``Lost sector`` — and most
    #: of them disagreed. Where they did, the settings page's label wins: it is the most
    #: deliberate copy, and the one an operator reads while configuring.
    display_name: str
    #: One-line description for the feed's headline settings row (its toggle row when it
    #: has one, otherwise its channel row).
    desc: str
    #: The ``/autopost`` subcommand, when it is not the slug. Only one LIVE feed
    #: diverges (``twab`` → ``twid``), and the value is load-bearing: changing it
    #: re-registers a Discord command that users have muscle memory for. A retired entry
    #: may also carry one — it is the record of what that feed's command WAS, and
    #: nothing registers it (see :attr:`retired`).
    command_name: str | None = None
    #: What ``/autopost <command> ✓`` calls the feed in its confirmation, when the
    #: canonical name would read oddly under a command of a different name. Deliberately
    #: bounded to entries that set :attr:`command_name` — see the invariant in
    #: ``tests/test_feeds.py``.
    follow_confirmation_name: str | None = None
    #: Retired: the feed no longer exists, and this entry is its headstone. Nothing
    #: produces it, no command registers for it, it has no row on the feeds page — see
    #: :data:`LIVE` for the set every one of those reads. It stays in the catalog
    #: because retiring a feed does not delete its past: ``AutopostDailyStat`` rows,
    #: ``MirroredChannel`` rows and a ``<slug>_channel`` setting all outlive it, and
    #: every one of them is keyed by :attr:`slug`. A flag keeps the one thing that can
    #: name them — this entry — while a deletion would throw it away and leave the rest
    #: of the tree guessing from a leftover DB row.
    retired: bool = False

    @property
    def channel_key(self) -> str:
        """This feed's ``AutoPostSettings.name`` row holding its channel id.

        The ``"_channel"`` suffix keeps a feed's on/off (``enabled``) and its where
        (``value``) independently readable and writable, which is why the channel is not
        just stored on the toggle row.
        """
        return f"{self.slug}_channel"

    @property
    def has_toggle(self) -> bool:
        """Whether this feed has an ``enabled`` produce toggle — see
        :class:`FeedKind`."""
        return self.kind is FeedKind.ANCHOR_CRON

    @property
    def effective_command_name(self) -> str:
        """The ``/autopost`` subcommand actually registered for this feed."""
        return self.command_name or self.slug

    @property
    def confirmation_name(self) -> str:
        """What the ``/autopost`` confirmation calls this feed."""
        return self.follow_confirmation_name or self.display_name


#: Every followable, in feeds-page order: the six anchor produces on a schedule first
#: (each a toggle group), then the five it does not (each a single channel row).
#: Anchor's page splits that second half again — see :class:`FeedKind` on why the fact
#: that does it is not stored here. Descriptions are the feeds page's own copy.
#:
#: **Retiring a feed sets :attr:`~Followable.retired`; it never deletes the entry.**
#: Deletion was tried first and is the wrong seam: the slug outlives the feed in three
#: stores, so throwing the entry away leaves nothing able to name them, and the tree
#: ends up inferring "this used to be a feed" from a leftover ``<slug>_channel`` row —
#: a guess that cannot tell a retirement from a rename, since a rename leaves the old
#: row in place too. The flag is that fact stated instead of reconstructed.
#:
#: Read :data:`LIVE`, not this tuple, anywhere a feed is produced, followed, commanded
#: or configured. Read this tuple (or :data:`FEEDS`) where a slug has to be resolved to
#: a name — the stats page, the mirror log, ``/mirror source_details`` — because those
#: are looking at history, and history contains retired feeds.
#:
#: Retiring is still two edits: flag the entry, and delete the feed's beacon extension
#: module, which is where its ``/autopost`` subcommand and navigator are registered by
#: hand. ``follow_control_command_maker`` refuses a retired slug rather than quietly
#: registering a command for a feed that no longer exists, so forgetting the second
#: edit fails loudly at import. Anchor's ``/feed/<name>/…`` routes are not in scope
#: either way: they resolve through ``dd.anchor.autopost.registered_feeds()``, a
#: producer registry unrelated to this catalog. Un-retiring is the same two edits back.
#:
#: **It unfollows nobody, on purpose.** The ``MirroredChannel`` rows are the follower
#: list, and beacon's legacy fan-out gates on ``get_or_fetch_all_srcs()`` — raw channel
#: ids this catalog knows nothing about — so they outlive a retirement and a feed can
#: come back without every guild following again from zero. Nothing fans out meanwhile
#: because the upstream channel is dormant, which is an assumption about someone else's
#: Discord channel rather than anything this repo enforces.
#:
#: Accepted costs of keeping them: ``/autopost <name>`` was the only in-bot off switch
#: for a legacy mirror (a non-legacy follower is a Discord channel-follow webhook, which
#: its admin can still remove under Server Settings → Integrations, and which nothing
#: here probes or corrects); and ``reachability_sweep`` keeps probing the retained
#: legacy rows' destinations, so one whose destination goes bad is still auto-disabled.
FOLLOWABLES: tuple[Followable, ...] = (
    Followable(
        "lost_sector",
        FeedKind.ANCHOR_CRON,
        "Lost Sector",
        "Today's Lost Sector — location, champions, and shields.",
    ),
    Followable(
        "xur",
        FeedKind.ANCHOR_CRON,
        "Xûr",
        "Xûr's weekend location and inventory.",
    ),
    Followable(
        "eververse",
        FeedKind.ANCHOR_CRON,
        "Eververse",
        "This week's Eververse featured items and Bright Dust.",
    ),
    Followable(
        "ada",
        FeedKind.ANCHOR_CRON,
        "Ada-1",
        "Ada-1's weekly rotating shaders.",
    ),
    Followable(
        "portal_ops",
        FeedKind.ANCHOR_CRON,
        "Portal Ops",
        "Today's featured Portal Ops and their guaranteed rewards.",
    ),
    Followable(
        "iron_banner",
        FeedKind.ANCHOR_CRON,
        "Iron Banner",
        "Iron Banner weeks — dates, game modes, bonus focus pool, and guide link.",
    ),
    # TWAB/TWID: the command has been `/autopost twid` since the post was renamed "This
    # Week In Destiny", while the feed slug and the channel row kept the older name.
    Followable(
        "twab",
        FeedKind.UNSCHEDULED,
        "This Week At Bungie",
        "The Kyber channel TWAB posts follow from.",
        command_name="twid",
        follow_confirmation_name="TWID",
    ),
    Followable(
        "trials",
        FeedKind.UNSCHEDULED,
        "Trials of Osiris",
        "The Kyber channel this feed posts to.",
    ),
    Followable(
        "weekly_reset",
        FeedKind.UNSCHEDULED,
        "Weekly Reset",
        "The Kyber channel this feed posts to.",
    ),
    Followable(
        "weekly_nightfall",
        FeedKind.UNSCHEDULED,
        "Weekly Nightfall",
        "The Kyber channel weekly nightfall posts followed from.",
        command_name="nightfall",
        follow_confirmation_name="Nightfall",
        retired=True,
    ),
    Followable(
        "free_games",
        FeedKind.UNSCHEDULED,
        "Free Games",
        "The Kyber channel free-games posts follow from.",
    ),
    Followable(
        "emblems_and_cosmetics",
        FeedKind.UNSCHEDULED,
        "Emblems & Cosmetics",
        "The Kyber channel emblems/cosmetics posts follow from.",
    ),
)

#: The feeds that still exist. THE set for producing, following, commanding and
#: configuring — anything that acts on a feed rather than reading about one.
LIVE: tuple[Followable, ...] = tuple(f for f in FOLLOWABLES if not f.retired)

#: The headstones. Display and history only; see :attr:`Followable.retired`.
RETIRED: tuple[Followable, ...] = tuple(f for f in FOLLOWABLES if f.retired)

#: :data:`FOLLOWABLES` keyed by slug, for the lookup every consumer actually wants.
#: Deliberately covers retired feeds: this is the map a historical row is resolved
#: through, and a ``KeyError`` on a slug the database still holds is not an answer.
FEEDS: dict[str, Followable] = {f.slug: f for f in FOLLOWABLES}
