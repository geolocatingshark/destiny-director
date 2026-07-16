import typing as t

import attr

_T = t.TypeVar("_T")


class Minutes(int):
    pass


class CyclicList(list[_T]):
    """A list whose integer indexing wraps modulo its length.

    ``x[n]`` returns ``x[n % len(x)]`` for any integer ``n`` (negatives wrap forward, as
    Python's ``%`` follows the divisor's sign), so a fixed-length rotation is indexable
    by an ever-growing day/week counter. Slicing is untouched. Generalises the
    ``str``-only :class:`EntityRotation` to any element type (e.g. entry dicts).
    """

    def __getitem__(self, index: int) -> _T:
        return super().__getitem__(index % len(self))


def _parse_counts(count: str) -> int:
    if count == "":
        return 0
    elif count == "?":
        return -1
    else:
        return int(count)


class EntityRotation(list[str]):
    def __init__(self, entity_list: list[str]):
        super().__init__()
        self.extend(entity_list)

    def __getitem__(self, days_since_reference_date: int) -> str:
        # Returns the entity according to the rotation for
        # the number of days_since_reference_date
        # Use as follows:
        # x[days_since_reference_date]
        # where x is an EntityRotation instance
        return super().__getitem__(days_since_reference_date % len(self))


@attr.s
class SectorV1Compat:
    name: str = attr.ib()
    shortlink_gfx: str = attr.ib()
    reward: str | None = attr.ib(default=None)
    champions: str | None = attr.ib(default=None)
    shields: str | None = attr.ib(default=None)
    burn: str | None = attr.ib(default=None)
    modifiers: str | None = attr.ib(default=None)
    overcharged_weapon: str | None = attr.ib(default=None)
    surge: str | None = attr.ib(default=None)
