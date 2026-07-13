"""Domain model for the Destiny 2 "legacy" world-activity rotations.

Each *destination* (Neomuna, the Moon, Dares of Eternity, …) is one JSON document made
of independent *activities* (Terminal Overload, Wellspring, …). Most activities are made
of *elements* — the weapon, location, boss, … — and **each element is its own
independent, variable-length cycle**: a fixed list of values indexed by the number of
days (or weeks) since a common ``reference_date``. So within one activity a 2-cycle mode
can sit beside a 4-cycle boss, and daily and weekly activities coexist in one document.

A few activities are instead **set-based** (``kind: "sets"``, e.g. the Dares of Eternity
loot): a pool of fixed *sets* (each a bundle of gear) plus a weekly *schedule* naming
which set is live each week. Resolving one picks the scheduled set for the date.

Pure — no network. Built from the DB JSON store (see ``dd.common.legacy_activities``).
"""

from __future__ import annotations

import datetime as dt
import typing as t

import attr

from .utils import CyclicList

# Destiny's daily/weekly reset is 17:00 UTC. Weekly activities reset on Tuesdays, so a
# document's ``reference_date`` must be a Tuesday for ``days // 7`` week boundaries to
# align with the real reset (daily-only destinations are insensitive to the weekday).
_RESET_OFFSET = dt.timedelta(hours=17)

Cadence = t.Literal["daily", "weekly"]


@attr.s
class LegacyElement:
    """One independently-rotating field of an activity (its own cyclic list)."""

    name: str = attr.ib()
    values: CyclicList[str] = attr.ib()


@attr.s
class LegacySet:
    """A fixed bundle of gear for a set-based activity (one Dares of Eternity set).

    A set is identified by its :attr:`name`; the schedule references sets by that same
    name (mirroring how the lost-sector schedule references sectors by name).
    """

    name: str = attr.ib()
    weapons: list[str] = attr.ib()
    armor: list[str] = attr.ib()


@attr.s
class LegacyActivity:
    """One rotating activity within a destination (e.g. Terminal Overload).

    ``kind == "elements"`` uses :attr:`elements`; ``kind == "sets"`` uses
    :attr:`schedule` (a weekly cycle of set ids) + :attr:`sets` (id → set).
    """

    key: str = attr.ib()
    title: str = attr.ib()
    cadence: str = attr.ib()
    kind: str = attr.ib(default="elements")
    # Ordered elements; each carries its own cycle, so lengths may differ.
    elements: list[LegacyElement] = attr.ib(factory=list)
    # Set-based: the weekly cycle of set ids, and the set pool keyed by id.
    schedule: CyclicList[str] = attr.ib(factory=CyclicList)
    sets: dict[str, LegacySet] = attr.ib(factory=dict)


@attr.s
class ResolvedSet:
    """A set-based activity resolved to the set live on a given date."""

    name: str = attr.ib()
    weapons: list[str] = attr.ib()
    armor: list[str] = attr.ib()


@attr.s
class ResolvedActivity:
    """An activity resolved for a date: element values, or a live set (set-based)."""

    key: str = attr.ib()
    title: str = attr.ib()
    cadence: str = attr.ib()
    # Ordered element name → value; an empty string means "no data this period" (TBC).
    values: dict[str, str] = attr.ib(factory=dict)
    # Present only for set-based activities; None when the scheduled set is missing.
    set: ResolvedSet | None = attr.ib(default=None)

    @property
    def is_empty(self) -> bool:
        """Whether there's nothing to show for this date (renders as TBC)."""
        if self.set is not None:
            return not (self.set.weapons or self.set.armor)
        return not any(self.values.values())


@attr.s
class LegacyRotation:
    """A whole destination: a start date plus its activities, indexable by date."""

    start_date: dt.datetime = attr.ib()
    activities: list[LegacyActivity] = attr.ib()

    @classmethod
    def from_json(cls, doc: dict[str, t.Any]) -> LegacyRotation:
        """Build from a stored JSON document (pure, no network).

        ``reference_date`` is the calendar start date; ``start_date`` is midnight UTC of
        that date plus the 17:00 reset offset. Tolerant: an element with no ``values``
        builds with an empty cycle and later resolves to an empty value (the "TBC" path)
        rather than raising.
        """
        reference_date = dt.date.fromisoformat(doc["reference_date"])
        start_date = (
            dt.datetime(
                reference_date.year,
                reference_date.month,
                reference_date.day,
                tzinfo=dt.UTC,
            )
            + _RESET_OFFSET
        )

        activities: list[LegacyActivity] = []
        for activity in doc["activities"]:
            kind = activity.get("kind", "elements")
            if kind == "sets":
                activities.append(
                    LegacyActivity(
                        key=activity["key"],
                        title=activity["title"],
                        cadence=activity["cadence"],
                        kind="sets",
                        schedule=CyclicList(activity.get("schedule", [])),
                        sets={
                            s["name"]: LegacySet(
                                name=s["name"],
                                weapons=list(s.get("weapons", [])),
                                armor=list(s.get("armor", [])),
                            )
                            for s in activity.get("sets", [])
                        },
                    )
                )
            else:
                activities.append(
                    LegacyActivity(
                        key=activity["key"],
                        title=activity["title"],
                        cadence=activity["cadence"],
                        elements=[
                            LegacyElement(
                                name=element["name"],
                                values=CyclicList(element["values"]),
                            )
                            for element in activity["elements"]
                        ],
                    )
                )

        return cls(start_date, activities)

    def to_json(self, *, version: int = 1) -> dict[str, t.Any]:
        """Serialise back to the JSON document shape (inverse of :meth:`from_json`)."""
        return {
            "version": version,
            # start_date is midnight-UTC-of-reference-date + (<24h) reset offset, so its
            # UTC date is exactly the reference date.
            "reference_date": self.start_date.astimezone(dt.UTC).date().isoformat(),
            "activities": [self._activity_to_json(a) for a in self.activities],
        }

    @staticmethod
    def _activity_to_json(activity: LegacyActivity) -> dict[str, t.Any]:
        if activity.kind == "sets":
            return {
                "key": activity.key,
                "title": activity.title,
                "cadence": activity.cadence,
                "kind": "sets",
                "schedule": list(activity.schedule),
                "sets": [
                    {
                        "name": s.name,
                        "weapons": list(s.weapons),
                        "armor": list(s.armor),
                    }
                    for s in activity.sets.values()
                ],
            }
        return {
            "key": activity.key,
            "title": activity.title,
            "cadence": activity.cadence,
            "elements": [
                {"name": element.name, "values": list(element.values)}
                for element in activity.elements
            ],
        }

    def __call__(self, date: dt.datetime | None = None) -> list[ResolvedActivity]:
        """Resolve every activity's elements to their live values on ``date`` (now).

        Daily activities index by whole days since the start date; weekly activities by
        whole weeks (``days // 7``). Each element wraps modulo *its own* length, so a
        short cycle and a long cycle in the same activity advance independently.
        """
        date = date if date is not None else dt.datetime.now(tz=dt.UTC)
        days_since_ref = (date - self.start_date).days

        resolved: list[ResolvedActivity] = []
        for activity in self.activities:
            index = (
                days_since_ref if activity.cadence == "daily" else days_since_ref // 7
            )
            if activity.kind == "sets":
                live_set: ResolvedSet | None = None
                if activity.schedule:
                    scheduled = activity.sets.get(activity.schedule[index])
                    if scheduled is not None:
                        live_set = ResolvedSet(
                            name=scheduled.name,
                            weapons=list(scheduled.weapons),
                            armor=list(scheduled.armor),
                        )
                resolved.append(
                    ResolvedActivity(
                        key=activity.key,
                        title=activity.title,
                        cadence=activity.cadence,
                        set=live_set,
                    )
                )
            else:
                values = {
                    element.name: (element.values[index] if element.values else "")
                    for element in activity.elements
                }
                resolved.append(
                    ResolvedActivity(
                        key=activity.key,
                        title=activity.title,
                        cadence=activity.cadence,
                        values=values,
                    )
                )

        return resolved

    @property
    def step(self) -> dt.timedelta:
        """Pagination step: one day if any activity is daily, else one week."""
        if any(activity.cadence == "daily" for activity in self.activities):
            return dt.timedelta(days=1)
        return dt.timedelta(days=7)
