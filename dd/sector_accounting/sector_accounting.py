from __future__ import annotations

import datetime as dt
import warnings
from collections import defaultdict
from typing import List, Self, Union

import attr
import gspread
import regex as re

from .utils import (
    EntityRotation,
    Minutes,
    _parse_counts,
    all_values_from_sheet,
)

# For future reference, this file pulls data from google sheets
# The library used to pull this data is gspread
# The spreadsheet file has headings which need to be removed
# from our pulled data before being used
# The [1:] slices in from_gspread methods
# omit the headings in the google sheets file


# Regex for splitting based on commands and ampersands
# This is used to split the surge column in the lost sector sheet
re_split_list = re.compile(r"[,&]")


@attr.s
class DifficultySpecificSectorData:
    """Represents sector data for specific difficulties

    Note: for all counts, -1 means at least 1 and 0 means none

    Attributes:
        barrier_champions (int): Number of barrier champions in the lost sector.
        overload_champions (int): Number of overload champions in the lost sector.
        unstoppable_champions (int): Number of unstoppable champions in the lost sector.
        arc_shields (int): Number of arc shields in the lost sector.
        void_shields (int): Number of void shields in the lost sector.
        solar_shields (int): Number of solar shields in the lost sector.
        stasis_shields (int): Number of stasis shields in the lost sector.
        strand_shields (int): Number of strand shields in the lost sector.
        modifiers (str): Modifiers on the lost sector.
        champions_list (List[str]): List of champions in the lost sector.
        champions (str): Comma separated list of champions in the lost sector.
        shields_list (List[str]): List of shields in the lost sector.
        shields (str): Comma separated list of shields in the lost sector.
    """

    barrier_champions = attr.ib(0, converter=_parse_counts)
    overload_champions = attr.ib(0, converter=_parse_counts)
    unstoppable_champions = attr.ib(0, converter=_parse_counts)
    arc_shields = attr.ib(0, converter=_parse_counts)
    void_shields = attr.ib(0, converter=_parse_counts)
    solar_shields = attr.ib(0, converter=_parse_counts)
    stasis_shields = attr.ib(0, converter=_parse_counts)
    strand_shields = attr.ib(0, converter=_parse_counts)
    modifiers = attr.ib("")

    @property
    def champions_list(self) -> List[str]:
        champions = []
        if self.barrier_champions != 0:
            champions.append("Barrier")
        if self.overload_champions != 0:
            champions.append("Overload")
        if self.unstoppable_champions != 0:
            champions.append("Unstoppable")
        return champions

    @property
    def champions(self) -> str:
        return ", ".join(self.champions_list) or "None"

    @property
    def shields_list(self) -> List[str]:
        shields = []
        if self.arc_shields != 0:
            shields.append("Arc")
        if self.void_shields != 0:
            shields.append("Void")
        if self.solar_shields != 0:
            shields.append("Solar")
        if self.stasis_shields != 0:
            shields.append("Stasis")
        if self.strand_shields != 0:
            shields.append("Strand")
        return shields

    @property
    def shields(self) -> str:
        return ", ".join(self.shields_list) or "None"

    def __bool__(self):
        return bool(
            self.barrier_champions
            or self.overload_champions
            or self.unstoppable_champions
            or self.arc_shields
            or self.void_shields
            or self.solar_shields
            or self.stasis_shields
            or self.strand_shields
            or self.modifiers
        )


@attr.s
class Sector:
    """Represents an in game lost sector.

    Attributes:
        name (str): Name of the lost sector.
        reward (str): Name of the reward for the lost sector.
        surge (str): Surge of the lost sector.
        legendary_rewards (str): Legendary rewards for the lost sector.
        threat (str): Threat of the lost sector.
        overcharged_weapon (str): Overcharged weapon of the lost sector.
        shortlink_gfx (str): Shortlink to the lost sector's graphic.
        legend_data (DifficultySpecificSectorData): Data for the lost sector
            on legend difficulty.
        master_data (DifficultySpecificSectorData): Data for the lost sector
            on master difficulty.
    """

    # From "Lost Sectors (Internal)" sheet 0
    name = attr.ib(type=str)
    reward = attr.ib("")
    surge = attr.ib("")
    legendary_rewards = attr.ib("")
    # From "Lost Sector Shield & Champion Counts" sheet 2
    threat = attr.ib("")
    overcharged_weapon = attr.ib("")
    shortlink_gfx = attr.ib("")
    # From "Lost Sector Shield & Champion Counts" sheet 0
    expert_data = attr.ib(DifficultySpecificSectorData())
    # From "Lost Sector Shield & Champion Counts" sheet 1
    master_data = attr.ib(DifficultySpecificSectorData())

    @property
    def surges(self) -> List[str]:
        surges = re_split_list.split(self.surge)
        surges = [surge.strip() for surge in surges]
        return surges

    def __add__(self, other: Sector):
        if not self.name == other.name:
            raise ValueError("Cannot add sectors with different names")
        return Sector(
            self.name,
            self.reward or other.reward,
            self.surge or other.surge,
            self.legendary_rewards or other.legendary_rewards,
            self.threat or other.threat,
            self.overcharged_weapon or other.overcharged_weapon,
            self.shortlink_gfx or other.shortlink_gfx,
            self.expert_data or other.expert_data,
            self.master_data or other.master_data,
        )


class SectorData(dict):
    def __init__(
        self,
        general: gspread.Spreadsheet,
        legend: gspread.Spreadsheet,
        master: gspread.Spreadsheet,
    ):
        general = all_values_from_sheet(general, columns_are_major=False)[1:]
        legend = all_values_from_sheet(legend, columns_are_major=False)[1:]
        master = all_values_from_sheet(master, columns_are_major=False)[1:]

        for general_row, legend_row, master_row in zip(general, legend, master):
            sector: Sector = self.gspread_data_row_to_sector(
                general_row, legend_row, master_row
            )
            self[sector.name] = sector

    @staticmethod
    def gspread_data_row_to_sector(
        general_row: list, legend_row: list, master_row: list
    ) -> Sector:
        return Sector(
            name=general_row[0],
            threat=general_row[1],
            overcharged_weapon=general_row[2],
            shortlink_gfx=general_row[3],
            expert_data=DifficultySpecificSectorData(
                void_shields=legend_row[1],
                solar_shields=legend_row[2],
                arc_shields=legend_row[3],
                stasis_shields=legend_row[4],
                strand_shields=legend_row[5],
                barrier_champions=legend_row[6],
                overload_champions=legend_row[7],
                unstoppable_champions=legend_row[8],
                modifiers=legend_row[9],
            ),
            master_data=DifficultySpecificSectorData(
                void_shields=master_row[1],
                solar_shields=master_row[2],
                arc_shields=master_row[3],
                stasis_shields=master_row[4],
                strand_shields=master_row[5],
                barrier_champions=master_row[6],
                overload_champions=master_row[7],
                unstoppable_champions=master_row[8],
                modifiers=master_row[9],
            ),
        )


class SpreadsheetBackedData:
    @classmethod
    def from_gspread_url(
        cls,
        url: str,
        # Google API credentials, see https://docs.gspread.org/en/latest/oauth2.html
        credentials: dict,
        **kwargs,
    ) -> Self:
        # Instantiates the spreadsheet, only uses the first worksheet by default
        # Ignore the client_factory deprecation warning
        # it looks like gspread.http_client is not implmented as of 5.10
        warnings.filterwarnings(
            "ignore",
            message=(
                r"[Deprecated][in version 6.0.0]: client_factory "
                + r"will be replaced by gspread.http_client types"
            ),
        )
        spreadsheet: gspread.Spreadsheet = gspread.service_account_from_dict(
            credentials
        ).open_by_url(url)
        return cls.from_gspread(spreadsheet, **kwargs)

    @classmethod
    def from_gspread(
        cls,
        worksheet: gspread.Spreadsheet,
    ) -> Self:
        raise NotImplementedError


@attr.s
class Rotation(SpreadsheetBackedData):
    start_date = attr.ib(type=dt.datetime)
    _sector_rot = attr.ib(type=defaultdict(EntityRotation))
    _surge_rot = attr.ib(type=EntityRotation)
    _sector_data = attr.ib(SectorData)

    @classmethod
    def from_gspread(
        cls,
        worksheet: gspread.Spreadsheet,
        buffer: Minutes = 10,  # in minutes
    ) -> Rotation:
        rotation_sheet = worksheet.get_worksheet(1)
        legend_sheet = worksheet.get_worksheet(2)
        master_sheet = worksheet.get_worksheet(3)
        general_sheet = worksheet.get_worksheet(4)
        values = all_values_from_sheet(rotation_sheet)

        self = cls(
            # Lost sector start date
            cls._start_date_from_gspread(rotation_sheet, buffer),
            sector_rot={
                "Cosmodrome": EntityRotation.from_gspread(values, 2),
                "The Dreaming City": EntityRotation.from_gspread(values, 3),
                "EDZ": EntityRotation.from_gspread(values, 4),
                "Europa": EntityRotation.from_gspread(values, 5),
                "The Moon": EntityRotation.from_gspread(values, 6),
                "Neomuna": EntityRotation.from_gspread(values, 7),
                "Nessus": EntityRotation.from_gspread(values, 8),
                "Pale Heart": EntityRotation.from_gspread(values, 9),
                "Throne World": EntityRotation.from_gspread(values, 10),
            },
            surge_rot=EntityRotation.from_gspread(values, 11),
            sector_data=SectorData(general_sheet, legend_sheet, master_sheet),
        )

        return self

    def __call__(self, date: Union[dt.datetime, None] = None) -> List[Sector]:
        # Returns the lost sector in rotation on date or for today by default
        date = date if date is not None else dt.datetime.now(tz=dt.timezone.utc)
        days_since_ref_date = (date - self.start_date).days

        sectors = []

        for sector_names in self._sector_rot.values():
            sector_name = sector_names[days_since_ref_date]
            sectors.append(
                Sector(name=sector_name, surge=self._surge_rot[days_since_ref_date])
                + self._sector_data[sector_name]
            )

        return sectors

    @staticmethod
    def _start_date_from_gspread(
        sheet: gspread.Worksheet,
        buffer: int = 10,  # in minutes
    ) -> dt.datetime:
        # Lost sector schedule start/reference date logic
        # Reset time is set to "buffer" minutes before destiny reset
        # This gives 10 minutes of tolerance in case of an early trigger
        # of a lost sector annoucement
        reset_time = dt.timedelta(hours=16, minutes=(60 - buffer))
        # Google sheets epoch date (reference date for all dates in sheets)
        google_sheets_epoch_date = dt.datetime(
            1899, 12, 30, 0, 0, 0, tzinfo=dt.timezone.utc
        )
        # Note that the reference date below is actually not a date,
        # it is the number of days since the google sheets epoch date
        # hence we need to convert this into a usable date before returning
        ls_reference_date = sheet.acell("A2", "UNFORMATTED_VALUE").value
        relative_ls_start_date = dt.timedelta(days=ls_reference_date)
        # Actual lost sector rotation start date
        start_date = relative_ls_start_date + google_sheets_epoch_date + reset_time
        return start_date

    def __len__(self):
        return len(self._sector_rot)
