from __future__ import annotations

import datetime as dt
from typing import List, Union

import gspread
from pytz import utc

# For future reference, this file pulls data from google sheets
# The library used to pull this data is gspread
# The spreadsheet file has headings which need to be removed
# from our pulled data before being used
# The [1:] slices in from_gspread methods
# omit the headings in the google sheets file


class Minutes(int):
    pass


class Sector:
    def __init__(self, name: str, shortlink_gfx: str):
        self.name = name
        self.shortlink_gfx = shortlink_gfx
        self.reward = None
        self.champions = None
        self.shields = None
        self.burn = None
        self.modifiers = None
        self.overcharged_weapon = None
        self.surge = None


class Rotation:
    def __init__(
        self,
        start_date: dt.datetime,
        reward_rotation: RewardRotation,
        lost_sector_rotation: SectorRotation,
        champion_rotation: ChampionRotation,
        shield_rotation: ShieldRotation,
        burn_rotation: BurnRotation,
        modifiers_rotation: ModifiersRotation,
        overcharged_weapon_rotation: OverchargedWeaponRotation,
        surge_rotation: SurgeRotation,
    ):
        self.start_date = start_date
        self._reward_rot = reward_rotation
        self._sector_rot = lost_sector_rotation
        self._champ_rot = champion_rotation
        self._shield_rot = shield_rotation
        self._burn_rot = burn_rotation
        self._modifiers_rot = modifiers_rotation
        self._overcharged_wep_rot = overcharged_weapon_rotation
        self._surge_rot = surge_rotation

    @classmethod
    def from_gspread_url(
        cls,
        url: str,
        credentials: dict,  # Google API credentials, see https://docs.gspread.org/en/latest/oauth2.html
        sheet_no: int = 0,
        buffer: Minutes = 10,  # buffer in minutes
    ) -> Rotation:
        # Instantiates the spreadsheet, only uses the first worksheet by default
        sheet = (
            gspread.service_account_from_dict(credentials)
            .open_by_url(url)
            .get_worksheet(sheet_no)
        )
        return cls.from_gspread(sheet, buffer)

    @classmethod
    def from_gspread(
        cls, sheet: gspread.models.Worksheet, buffer: Minutes = 10  # in minutes
    ) -> Rotation:
        # Lost sector start date
        start_date = cls._start_date_from_gspread(sheet, buffer)
        # Rewards rotation in order starting on the above date
        reward_rot = RewardRotation.from_gspread(sheet)
        # Lost sector rotation in order start on the above date
        sector_rot = SectorRotation.from_gspread(sheet)
        champ_rot = ChampionRotation.from_gspread(sheet)
        shield_rot = ShieldRotation.from_gspread(sheet)
        burn_rot = BurnRotation.from_gspread(sheet)
        modifiers_rot = ModifiersRotation.from_gspread(sheet)
        overcharged_wep_rot = OverchargedWeaponRotation.from_gspread(sheet)
        surge_rot = SurgeRotation.from_gspread(sheet)
        return cls(
            start_date,
            reward_rot,
            sector_rot,
            champ_rot,
            shield_rot,
            burn_rot,
            modifiers_rot,
            overcharged_wep_rot,
            surge_rot,
        )

    def __call__(self, date: Union[dt.datetime, None] = None) -> Sector:
        # Returns the lost sector in rotation on date or for today by default
        date = date if date is not None else dt.datetime.now(tz=utc)
        days_since_ref_date = (date - self.start_date).days

        sector = self._sector_rot[days_since_ref_date]

        sector.reward = self._reward_rot[days_since_ref_date]
        sector.champions = self._champ_rot[days_since_ref_date]
        sector.shields = self._shield_rot[days_since_ref_date]
        sector.burn = self._burn_rot[days_since_ref_date]
        sector.modifiers = self._modifiers_rot[days_since_ref_date]
        sector.overcharged_weapon = self._overcharged_wep_rot[days_since_ref_date]
        sector.surge = self._surge_rot[days_since_ref_date]

        return sector

    @staticmethod
    def _start_date_from_gspread(
        sheet: gspread.models.Worksheet, buffer: int = 10  # in minutes
    ) -> dt.datetime:
        # Lost sector schedule start/reference date logic
        # Reset time is set to "buffer" minutes before destiny reset
        # This gives 10 minutes of tolerance in case of an early trigger
        # of a lost sector annoucement
        reset_time = dt.timedelta(hours=16, minutes=(60 - buffer))
        # Google sheets epoch date (reference date for all dates in sheets)
        google_sheets_epoch_date = dt.datetime(1899, 12, 30, 0, 0, 0, tzinfo=utc)
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


class EntityRotation(list):
    def __init__(self, entity_list: list):
        self.extend(entity_list)

    def __getitem__(self, days_since_reference_date: int) -> str:
        # Returns the entity according to the rotation for
        # the number of days_since_reference_date
        # Use as follows:
        # x[days_since_reference_date]
        # where x is an EntityRotation instance
        return super().__getitem__(days_since_reference_date % len(self))


class SectorRotation(EntityRotation):
    """Class that keeps track of the lost sector schedule"""

    def __init__(self, ls_list: List[list]):
        super().__init__(ls_list)

    @classmethod
    def from_gspread(cls, sheet: gspread.models.Worksheet):
        ls_list = sheet.get("ls_list")[1:]
        return cls([Sector(*ls) for ls in ls_list])


class ChampionRotation(EntityRotation):
    def __init__(self, reward_list: List[str]):
        super().__init__(reward_list)

    @classmethod
    def from_gspread(cls, sheet: gspread.models.Worksheet):
        reward_list = sheet.col_values(4)[1:]
        return cls(reward_list)


class ShieldRotation(EntityRotation):
    def __init__(self, reward_list: List[str]):
        super().__init__(reward_list)

    @classmethod
    def from_gspread(cls, sheet: gspread.models.Worksheet):
        reward_list = sheet.col_values(5)[1:]
        return cls(reward_list)


class BurnRotation(EntityRotation):
    def __init__(self, reward_list: List[str]):
        super().__init__(reward_list)

    @classmethod
    def from_gspread(cls, sheet: gspread.models.Worksheet):
        reward_list = sheet.col_values(6)[1:]
        return cls(reward_list)


class ModifiersRotation(EntityRotation):
    def __init__(self, reward_list: List[str]):
        super().__init__(reward_list)

    @classmethod
    def from_gspread(cls, sheet: gspread.models.Worksheet):
        reward_list = sheet.col_values(7)[1:]
        return cls(reward_list)


class OverchargedWeaponRotation(EntityRotation):
    def __init__(self, reward_list: List[str]):
        super().__init__(reward_list)

    @classmethod
    def from_gspread(cls, sheet: gspread.models.Worksheet):
        reward_list = sheet.col_values(9)[1:]
        # reward_list = sheet.col_values(8)[1:]
        return cls(reward_list)


class SurgeRotation(EntityRotation):
    def __init__(self, reward_list: List[str]):
        super().__init__(reward_list)

    @classmethod
    def from_gspread(cls, sheet: gspread.models.Worksheet):
        reward_list = sheet.col_values(10)[1:]
        # reward_list = sheet.col_values(9)[1:]
        return cls(reward_list)


class RewardRotation(EntityRotation):
    def __init__(self, reward_list: List[str]):
        super().__init__(reward_list)

    @classmethod
    def from_gspread(cls, sheet: gspread.models.Worksheet):
        reward_list = sheet.col_values(8)[1:]
        # reward_list = sheet.col_values(10)[1:]
        return cls(reward_list)
