from itertools import dropwhile

import attr
import gspread
import gspread.utils


class Minutes(int):
    pass


def _parse_counts(count: str) -> int:
    if count == "":
        return 0
    elif count == "?":
        return -1
    else:
        return int(count)


def all_values_from_sheet(
    sheet: gspread.Worksheet, columns_are_major: bool = True
) -> list[list[str]]:
    # Returns all values from a sheet with columns as the major dimension
    if columns_are_major:
        return sheet.get_values(major_dimension=gspread.utils.Dimension.cols)
    else:
        return sheet.get_values(major_dimension=gspread.utils.Dimension.rows)


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

    @classmethod
    def from_gspread(
        cls, sheet_or_values: gspread.Worksheet | list[list[str]], column: int
    ):
        values = (
            all_values_from_sheet(sheet_or_values)
            if isinstance(sheet_or_values, gspread.Worksheet)
            else sheet_or_values
        )

        li = values[column][1:]

        # Remove trailing falsey values
        li = list(reversed(tuple(dropwhile(lambda x: not bool(x), reversed(li)))))

        return cls(li)


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
