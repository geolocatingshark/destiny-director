import typing as t

import hikari as h
import lightbulb as lb
from hmessage import HMessage

from ..common import cfg
from ..sector_accounting import sector_accounting
from . import utils


def _fmt_count(emoji: str, count: int, width: int) -> str:
    if count:
        return "{} x `{}`".format(
            emoji,
            str(count if count != -1 else "?").rjust(width, " "),
        )
    else:
        return ""


def format_counts(
    legend_data: sector_accounting.DifficultySpecificSectorData,
    master_data: sector_accounting.DifficultySpecificSectorData,
    emoji_dict: t.Dict[str, h.Emoji],
) -> str:
    len_bar = len(
        str(max(legend_data.barrier_champions, master_data.barrier_champions, key=abs))
    )
    len_oload = len(
        str(
            max(legend_data.overload_champions, master_data.overload_champions, key=abs)
        )
    )
    len_unstop = len(
        str(
            max(
                legend_data.unstoppable_champions,
                master_data.unstoppable_champions,
                key=abs,
            )
        )
    )
    len_arc = len(str(max(legend_data.arc_shields, master_data.arc_shields, key=abs)))
    len_void = len(
        str(max(legend_data.void_shields, master_data.void_shields, key=abs))
    )
    len_solar = len(
        str(max(legend_data.solar_shields, master_data.solar_shields, key=abs))
    )
    len_stasis = len(
        str(max(legend_data.stasis_shields, master_data.stasis_shields, key=abs))
    )
    len_strand = len(
        str(max(legend_data.strand_shields, master_data.strand_shields, key=abs))
    )

    data_strings = []

    for data in [legend_data, master_data]:
        champs_string = utils.space.figure.join(
            filter(
                None,
                [
                    _fmt_count(emoji_dict["barrier"], data.barrier_champions, len_bar),
                    _fmt_count(
                        emoji_dict["overload"], data.overload_champions, len_oload
                    ),
                    _fmt_count(
                        emoji_dict["unstoppable"],
                        data.unstoppable_champions,
                        len_unstop,
                    ),
                ],
            )
        )
        shields_string = utils.space.figure.join(
            filter(
                None,
                [
                    _fmt_count(emoji_dict["arc"], data.arc_shields, len_arc),
                    _fmt_count(emoji_dict["void"], data.void_shields, len_void),
                    _fmt_count(emoji_dict["solar"], data.solar_shields, len_solar),
                    _fmt_count(emoji_dict["stasis"], data.stasis_shields, len_stasis),
                    _fmt_count(emoji_dict["strand"], data.strand_shields, len_strand),
                ],
            )
        )
        data_string = f"{utils.space.figure}|{utils.space.figure}".join(
            filter(
                None,
                [
                    champs_string,
                    shields_string,
                ],
            )
        )
        data_strings.append(data_string)

    return (
        f"Expert:{utils.space.figure}"
        + data_strings[0]
        + f"\nMaster:{utils.space.hair}{utils.space.figure}"
        + data_strings[1]
    )


async def get_emoji_dict(bot: lb.BotApp):
    guild = bot.cache.get_guild(
        cfg.kyber_discord_server_id
    ) or await bot.rest.fetch_guild(cfg.kyber_discord_server_id)
    return {emoji.name: emoji for emoji in await guild.fetch_emojis()}
