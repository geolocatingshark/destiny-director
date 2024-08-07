import typing as t

import hikari as h
import regex as re

re_user_side_emoji = re.compile("(<a?)?:(\w+)(~\d)*:(\d+>)?")


def construct_emoji_substituter(
    emoji_dict: t.Dict[str, h.Emoji],
) -> t.Callable[[re.Match], str]:
    """Constructs a substituter for user-side emoji to be used in re.sub"""

    def func(match: re.Match) -> str:
        maybe_emoji_name = str(match.group(2))
        return str(
            emoji_dict.get(maybe_emoji_name)
            or emoji_dict.get(maybe_emoji_name.lower())
            or match.group(0)
        )

    return func


class space:
    zero_width = "\u200b"
    hair = "\u200a"
    six_per_em = "\u2006"
    thin = "\u2009"
    punctuation = "\u2008"
    four_per_em = "\u2005"
    three_per_em = "\u2004"
    figure = "\u2007"
    en = "\u2002"
    em = "\u2003"


def get_ordinal_suffix(day: int) -> str:
    return (
        {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        if day not in (11, 12, 13)
        else "th"
    )
