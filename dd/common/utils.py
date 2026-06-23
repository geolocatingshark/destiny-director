import asyncio as aio
import logging
import typing as t

import aiohttp
import hikari as h
import regex as re

from . import cfg
from .discord_logging import identity_for_exc, reference_code

re_user_side_emoji = re.compile(r"(<a?)?:(\w+)(~\d)*:(\d+>)?")

# A Discord channel link embeds the guild id then the channel id; a message link adds
# the message id as a third segment. These let commands accept links/mentions/ids for
# channels in *other* servers — the slash-command channel option type can't, since its
# picker only lists channels in the guild the command was invoked from.
_re_channel_link = re.compile(
    r"(?:https?://)?(?:\w+\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)"
)
_re_channel_mention = re.compile(r"<#(\d+)>")
_re_message_link = re.compile(
    r"(?:https?://)?(?:\w+\.)?discord(?:app)?\.com/channels/\d+/(\d+)/(\d+)"
)


def parse_channel_ref(value: str) -> tuple[int, int | None]:
    """Parse a channel link, channel mention, or raw id.

    Returns ``(channel_id, guild_id)``; ``guild_id`` is only populated when a full
    channel link is supplied (a mention or bare id carries no guild). Lets commands
    target channels outside the current server, which the slash-command channel option
    type cannot.
    """
    value = value.strip()
    if match := _re_channel_link.search(value):
        guild_id, channel_id = int(match.group(1)), int(match.group(2))
        return channel_id, guild_id
    if match := _re_channel_mention.search(value):
        return int(match.group(1)), None
    try:
        return int(value), None
    except ValueError as e:
        raise ValueError(f"{value!r} is not a channel link, mention, or id") from e


def parse_message_link(value: str) -> tuple[int, int]:
    """Parse a Discord message link into ``(channel_id, message_id)``.

    Accepts ``.../channels/<guild_id>/<channel_id>/<message_id>`` (the guild segment
    is ignored — a message is uniquely addressed by its channel and id).
    """
    value = value.strip()
    if match := _re_message_link.search(value):
        return int(match.group(1)), int(match.group(2))
    raise ValueError(f"{value!r} is not a Discord message link")


# lightbulb registers global commands under guild key 0, so a guild id of 0 in a
# ``guilds=`` list silently turns a guild-scoped command into a global one.
GLOBAL_COMMAND_KEY = 0


def guild_scope(*guild_ids: int) -> list[int]:
    """Build a ``guilds=`` list safe to pass to lightbulb, dropping the 0 sentinel.

    A guild id of ``0`` is lightbulb's global-command key, so letting it through
    would register a guild-scoped command globally. Drop any such ids (warning when
    we do, since it usually means a guild-id env var is unset), and raise if nothing
    valid remains rather than registering globally by accident. Non-zero sentinels
    like ``-1`` are kept — they harmlessly scope to a nonexistent guild.
    """
    scoped = [gid for gid in guild_ids if gid != GLOBAL_COMMAND_KEY]
    if len(scoped) != len(guild_ids):
        logging.getLogger("main/" + __name__).warning(
            "Dropped guild id(s) equal to the global-command key (0) from a command "
            "registration scope; check that guild-id env vars are set."
        )
    if not scoped:
        raise ValueError(
            "Guild registration scope collapsed to empty after removing the "
            "global-command key (0); refusing to register globally by accident."
        )
    return list(dict.fromkeys(scoped))  # de-dupe, preserve order


async def fetch_emoji_dict(bot: h.GatewayBot):
    guild = bot.cache.get_guild(
        cfg.kyber_discord_server_id
    ) or await bot.rest.fetch_guild(cfg.kyber_discord_server_id)
    return {emoji.name: emoji for emoji in await guild.fetch_emojis()}


def construct_emoji_substituter(
    emoji_dict: dict[str, h.Emoji],
) -> t.Callable[[t.Any], str]:
    """Constructs a substituter for user-side emoji to be used in re.sub"""

    def func(match: t.Any) -> str:
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


async def update_status(bot: h.GatewayBot, guild_count: int, test_env: bool):
    await bot.update_presence(
        activity=h.Activity(
            name=f"{guild_count} servers : )" if not test_env else "DEBUG MODE",
            type=h.ActivityType.LISTENING,
        )
    )


# Cap link-following HTTP requests so a hung redirect host can't block a
# coroutine indefinitely (aiohttp's implicit default is a 5-minute total).
_LINK_FOLLOW_TIMEOUT = aiohttp.ClientTimeout(total=30)


async def follow_link_single_step(
    url: str, logger: logging.Logger | None = None
) -> str:
    if logger is None:
        logger = logging.getLogger("main/" + __name__)
    async with aiohttp.ClientSession(timeout=_LINK_FOLLOW_TIMEOUT) as session:
        retries = 10
        retry_delay = 10
        for i in range(retries):
            async with session.get(url, allow_redirects=False) as resp:
                try:
                    return resp.headers["Location"]
                except KeyError:
                    # If we can't find the location key, warn and return the
                    # provided url itself
                    if resp.status >= 400:
                        logger.error(
                            "Could not find redirect for url "
                            + f"{url}, (status {resp.status})"
                        )
                        if i < retries - 1:
                            logger.error("Retrying...")
                        await aio.sleep(retry_delay)
                        continue
                    else:
                        return url
        return url


def followable_name(*, id: int) -> str | int:
    """Return the configured name for a followable channel id, or the id itself."""
    return next((key for key, value in cfg.followables.items() if value == id), id)


class FriendlyValueError(ValueError):
    pass


def check_number_of_layers(
    ln_names: t.Sequence[t.Any] | int, min_layers: int = 1, max_layers: int = 3
):
    """Raises FriendlyValueError on too many layers of commands

    This is a simple helper function to check if ln_names is between min_layers and
    max_layers. If it is not, a FriendlyValueError is raised."""

    # ``ln_names`` is either an already-computed length (int) or a sequence of layer
    # names (list/tuple) to count. Narrow on int so the len() branch covers both
    # lists and tuples (callers pass ``*ln_names`` tuples).
    ln_name_length = ln_names if isinstance(ln_names, int) else len(ln_names)

    if ln_name_length > max_layers:
        raise FriendlyValueError(
            "Discord does not support slash "
            + f"commands with more than {max_layers} layers"
        )
    elif ln_name_length < min_layers:
        raise ValueError(f"Too few ln_names provided, need at least {min_layers}")


def ensure_session(sessionmaker):
    """Decorator for functions that optionally want an sqlalchemy async session

    Provides an async session via the `session` parameter if one is not already
    provided via the same.

    Caution: Always put below `@classmethod` and `@staticmethod`"""

    def ensured_session(
        f: t.Callable[..., t.Awaitable[t.Any]],
    ) -> t.Callable[..., t.Awaitable[t.Any]]:
        async def wrapper(*args: t.Any, **kwargs: t.Any) -> t.Any:
            session = kwargs.pop("session", None)
            if session is None:
                async with sessionmaker() as session, session.begin():
                    return await f(*args, **kwargs, session=session)
            else:
                return await f(*args, **kwargs, session=session)

        return wrapper

    return ensured_session


def accumulate[T](iterable: t.Sequence[T], /, empty_value: T | None = None) -> T:
    if not iterable:
        if empty_value is None:
            raise ValueError("accumulate() arg is an empty sequence")
        return empty_value
    final = iterable[0]
    for arg in iterable[1:]:
        final = final + arg  # ty: ignore[unsupported-operator]
    return final


async def discord_error_logger(
    e: Exception,
    error_reference: str | int | None = None,
    *,
    operation: str | None = None,
) -> str:
    """Surface an exception to the Discord alerts channel and the console.

    Routes through ``logging`` so the installed ``DiscordLogHandler`` renders a
    rich, deduplicated Components V2 alert (with traceback + severity) — it no
    longer sends to the channel directly. Returns the reference code shown to
    the user, which is deterministic per error identity so it matches the code
    on the resulting alert.

    ``operation`` is a short human label for what was being attempted (e.g.
    ``"Mirror update"``); when given it is surfaced in the alert header so the
    failure reads at a glance.
    """
    code = (
        str(error_reference) if error_reference else reference_code(identity_for_exc(e))
    )
    logging.getLogger("dd.error").error(
        "Error reference: %s",
        code,
        exc_info=e,
        extra={"dd_operation": operation} if operation else None,
    )
    return code
