from __future__ import annotations

import typing as t

import attr


@attr.s
class XurLocation:
    api_location_name: str = attr.ib()
    friendly_location_name: str | None = attr.ib(default=None)
    link: str | None = attr.ib(default=None)

    def __str__(self) -> str:
        str_ = ""
        if self.friendly_location_name:
            str_ += f"{self.friendly_location_name}"
        else:
            str_ += f"{self.api_location_name}"

        if self.link:
            str_ = f"[{str_}]({self.link})"

        return str_


class XurLocations(dict[str, XurLocation]):
    @classmethod
    def from_json(cls, doc: dict[str, t.Any]) -> XurLocations:
        """Build from a stored JSON document (DB store). Pure.

        Tolerant: a blank friendly name / link is normalised to ``None`` (so it renders
        as the raw API name), and ``__getitem__`` still falls back to the raw API name
        for any location not present in the document.
        """
        self: XurLocations = cls.__new__(cls)
        dict.__init__(self)

        for row in doc.get("locations", []):
            loc = XurLocation(
                api_location_name=row["api_location_name"],
                friendly_location_name=row.get("friendly_location_name") or None,
                link=row.get("link") or None,
            )
            self[loc.api_location_name] = loc

        return self

    def __getitem__(self, key: str) -> XurLocation:
        if key in self:
            return super().__getitem__(key)
        else:
            return XurLocation(
                api_location_name=key, friendly_location_name=None, link=None
            )
