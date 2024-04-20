from __future__ import annotations

import attr
import gspread

from .sector_accounting import SpreadsheetBackedData
from .utils import (
    all_values_from_sheet,
)

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self


@attr.s
class XurLocation:
    api_location_name: str = attr.ib()
    friendly_location_name: str = attr.ib()
    link: str = attr.ib()

    def __str__(self) -> str:
        str_ = ""
        if self.friendly_location_name:
            str_ += f"{self.friendly_location_name}"
        else:
            str_ += f"{self.api_location_name}"

        if self.link:
            str_ += f"[{str_}]({self.link})"

        return str_


class XurLocations(SpreadsheetBackedData, dict):
    @classmethod
    def from_gspread(
        cls: Self,
        sheet: gspread.Spreadsheet,
        api_location_name_col: int = 0,
        friendly_location_name_col: int = 1,
        link_col: int = 2,
    ) -> XurLocation:
        values = all_values_from_sheet(sheet.get_worksheet(7), columns_are_major=False)
        values = values[1:]

        self = cls()

        for row in values:
            loc = XurLocation(
                api_location_name=row[api_location_name_col],
                friendly_location_name=row[friendly_location_name_col],
                link=row[link_col],
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


@attr.s
class XurArmorSet:
    api_name_hunter = attr.ib()
    api_name_titan = attr.ib()
    api_name_warlock = attr.ib()
    friendly_name = attr.ib()
    link = attr.ib()

    def __str__(self) -> str:
        str_ = f"{self.friendly_name}"
        if self.link:
            str_ += f"[{str_}]({self.link})"

        return str_


class XurArmorSets(SpreadsheetBackedData, dict):
    @classmethod
    def from_gspread(
        cls: Self,
        sheet: gspread.Spreadsheet,
        api_name_hunter_col: int = 0,
        api_name_titan_col: int = 1,
        api_name_warlock_col: int = 2,
        friendly_name_col: int = 3,
        link_col: int = 4,
    ) -> XurArmorSet:
        values = all_values_from_sheet(sheet.get_worksheet(6), columns_are_major=False)
        values = values[1:]

        self = cls()

        for row in values:
            armor_set = XurArmorSet(
                api_name_hunter=row[api_name_hunter_col],
                api_name_titan=row[api_name_titan_col],
                api_name_warlock=row[api_name_warlock_col],
                friendly_name=row[friendly_name_col],
                link=row[link_col],
            )
            self[armor_set.api_name_hunter] = armor_set
            if armor_set.api_name_titan not in self:
                self[armor_set.api_name_titan] = armor_set
            if armor_set.api_name_warlock not in self:
                self[armor_set.api_name_warlock] = armor_set

        return self

    def __getitem__(self, key: str) -> XurArmorSet:
        if key in self:
            return super().__getitem__(key)
        else:
            return XurArmorSet(friendly_name=key, link=None)
