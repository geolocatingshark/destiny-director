# Copyright © 2019-present gsfernandes81

# This file is part of "dd" henceforth referred to as "destiny-director".

# destiny-director is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later version.

# "destiny-director" is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License along with
# destiny-director. If not, see <https://www.gnu.org/licenses/>.

# Define our custom navigator classes
import datetime as dt
import logging
import typing as t
from asyncio import Task, create_task, sleep
from random import randint
from typing import override

import hikari as h
import lightbulb as lb
import miru as m
import regex as re
from miru.ext import nav

from dd.hmessage import HMessage

from ..common.bot import CachedFetchBot
from ..common.cfg import (
    default_url,
    embed_default_color,
    navigator_timeout,
    url_regex,
)
from ..common.utils import accumulate, discord_error_logger, get_ordinal_suffix
from . import utils

NO_DATA_HERE_EMBED = h.Embed(title="No data here!", color=embed_default_color)

# Tolerance for binning Destiny reset-time messages into periods: a message
# posted up to this long before a reset still bins into the period it belongs to.
reset_time_tolerance = dt.timedelta(minutes=60)

# Unicode play (▶) / reverse (◀) triangles used as the default next/prev button
# emoji. Defined as module-level constants so they are not constructed in
# function-argument defaults (ruff B008).
NEXT_PAGE_EMOJI = chr(9654)
PREV_PAGE_EMOJI = chr(9664)


class DateRangeDict(dict[dt.datetime, HMessage]):
    """Dict with keys that are contiguous date ranges up to limits

    The keys of the backing dict are the start of the date ranges.
    The keys received by __getitem__ are rounded down to the nearest date
    provided it is within DateRangeDict.period: dt.timedelta
    If the key provided is an int, then it is interpreted as n periods
    since the current datetime rounded down.

    period: dt.timedelta
        The period between each key

    limits: tuple[dt.datetime, dt.datetime]
        The upper and lower bounds of the dict"""

    def __init__(
        self,
        period: dt.timedelta,
        limits: tuple[dt.datetime, dt.datetime] | None = None,
    ):
        if not isinstance(period, dt.timedelta):
            raise TypeError("period must be of type datetime.timedelta")

        self.period = period

        if limits:
            if len(limits) != 2:
                raise ValueError("limits must be a tuple of length 2")

            if not all(isinstance(limit, dt.datetime) for limit in limits):
                raise TypeError("limits must be a tuple of datetime.datetime")

            if limits[0] > limits[1]:
                raise ValueError("limits[0] must be less than limits[1]")

            if limits[1] - limits[0] < period:
                raise ValueError("limits must be at least one period apart")

            if (limits[1] - limits[0]) % period != dt.timedelta(0):
                raise ValueError("limits must be an integer multiple of period apart")

            self.limits = limits

    def round_down(
        self,
        key: dt.datetime,
        tolerance: dt.timedelta = reset_time_tolerance,
    ) -> dt.datetime:
        """Round down key to nearest period with tolerance in the negative direction

        The tolerance parameter allows for rounding up by its value"""
        return (
            (key + tolerance - self.limits[0]) // self.period
        ) * self.period + self.limits[0]

    def index_to_date(
        self, index: int, tolerance: dt.timedelta = reset_time_tolerance
    ) -> dt.datetime:
        """Return the datetime of the period at <index>"""
        return (
            self.round_down(dt.datetime.now(tz=dt.UTC), tolerance=tolerance)
            + index * self.period
        )

    @override
    def __getitem__(self, key: dt.datetime | int) -> HMessage:
        if isinstance(key, int):
            key = self.index_to_date(key)
        if not isinstance(key, dt.datetime):
            raise TypeError("Key must be of type datetime.datetime or int")

        self._truncate_outside_limits()

        if not (self.limits[0] <= key <= self.limits[1]):
            raise IndexError(f"Key {key} is not in range {self.limits}")

        key = self.round_down(key)
        return super().__getitem__(key)

    @override
    def __contains__(self, __key: object) -> bool:
        if isinstance(__key, int):
            __key = self.index_to_date(__key)
        if not isinstance(__key, dt.datetime):
            raise TypeError("Key must be of type datetime.datetime or int")

        self._truncate_outside_limits()

        if not (self.limits[0] <= __key <= self.limits[1]):
            return False

        __key = self.round_down(__key)
        return super().__contains__(__key)

    @override
    def __setitem__(self, key: dt.datetime, value: HMessage) -> None:
        if not isinstance(key, dt.datetime):
            raise TypeError("Key must be of type datetime.datetime")

        if not (self.limits[0] <= key <= self.limits[1]):
            raise IndexError(f"Key {key} is not in range {self.limits}")

        self._truncate_outside_limits()
        key = self.round_down(key)
        super().__setitem__(key, value)

    def _truncate_outside_limits(self) -> None:
        """Remove all keys outside our limits"""
        for key in list(self.keys()):
            if not (self.limits[0] <= key <= self.limits[1]):
                self.pop(key)

    def purge_history(self) -> None:
        """Removes all keys in the past including now"""
        now = dt.datetime.now(tz=dt.UTC)
        for key in list(self.keys()):
            if key <= now:
                self.pop(key)

    @staticmethod
    def nearest_limit_from_period_and_ref(period: dt.timedelta, ref: dt.datetime):
        """Return the nearest lower limit to ref that is an int multiple of period"""
        if not isinstance(period, dt.timedelta):
            raise TypeError("period must be of type datetime.timedelta")

        if not isinstance(ref, dt.datetime):
            raise TypeError("ref must be of type datetime.datetime")

        now = dt.datetime.now(tz=dt.UTC)
        return ((now - ref) // period) * period + ref


class NavigatorView(nav.NavigatorView):
    def __init__(
        self,
        *,
        pages: "NavPages",
        timeout: float | int | dt.timedelta | None = navigator_timeout,
        autodefer: bool = True,
        allow_start_on_blank_page: bool = False,
        display_date_offset: dt.timedelta = dt.timedelta(days=0),
    ) -> None:
        ### hikari-miru NavigatorView init ###
        # The only differences between this and the original is that
        # the pages object is not checked to be non-empty and
        # the default buttons are always added to the view
        self._pages: NavPages = pages
        self._current_page: int = 0
        self._ephemeral: bool = False
        # The last interaction received, used for inter-based handling
        self._inter: h.MessageResponseMixin[t.Any] | None = None
        super(nav.NavigatorView, self).__init__(timeout=timeout, autodefer=autodefer)
        self.display_date_offset = display_date_offset

        default_buttons = self.get_default_buttons()
        for default_button in default_buttons:
            self.add_item(default_button)

        ### hikari-miru NavigatorView init end ###

        if allow_start_on_blank_page:
            self.current_page = 0
        else:
            # Set current page to the first non blank page
            for page_no in range(0, -self.pages.history_len, -1):
                if page_no in self.pages:
                    self.current_page = page_no
                    break
            else:
                self.current_page = 0

    @override
    async def send(
        self,
        to: h.SnowflakeishOr[h.TextableChannel]
        | h.MessageResponseMixin[t.Any]
        | m.Context[t.Any],
        *,
        start_at: int | None = None,
        ephemeral: bool = False,
        responded: bool = False,
    ):
        # Override the default page number of 0 with the current page as set by init
        return await super().send(
            to,
            start_at=start_at if start_at is not None else self.current_page,
            ephemeral=ephemeral,
            responded=responded,
        )

    @override
    def _get_page_payload(
        self, page: str | h.Embed | t.Sequence[h.Embed] | nav.Page | HMessage
    ) -> t.MutableMapping[str, t.Any]:
        """Get the page content that is to be sent."""

        if not isinstance(page, HMessage):
            raise TypeError(
                f"Expected type 'HMessage' to send as page, "
                f"not '{page.__class__.__name__}'."
            )

        return_dict = page.to_message_kwargs()
        return_dict["components"] = self

        if self.ephemeral:
            return_dict["flags"] = h.MessageFlag.EPHEMERAL

        return return_dict

    @override
    async def send_page(
        self, context: m.Context[t.Any], page_index: int | None = None
    ) -> None:
        """Send a page, editing the original message.

        Parameters
        ----------
        context : Context
            The context object that should be used to send this page
        page_index : Optional[int], optional
            The index of the page to send, if not specifed, sends the current
            page, by default None
        """
        if page_index is not None:
            self.current_page = page_index

        page = self.pages[self.current_page]

        for button in self.children:
            if isinstance(button, nav.NavItem):
                await button.before_page_change()

        payload = self._get_page_payload(page)

        self._inter = context.interaction  # Update latest inter

        if not (payload.get("attachment") or payload.get("attachments")):
            # Ensure that payload does not have attachments as a key
            # even if it is a Falsey value
            payload.pop("attachments", None)
            # Set payload attachment to None if no attachments are returned
            # from _get_page_payload to make sure discord clears all atachments
            # in view.
            # Note: attachments=[] does not clear attachments.
            payload = {"attachment": None, **payload}

        await context.edit_response(**payload)  # ty: ignore[invalid-argument-type]

    @override
    def get_default_buttons(self) -> list[nav.NavButton]:
        if (self.pages.history_len + self.pages.lookahead_len) == 1:
            return []
        else:
            return [
                PrevButton(),
                IndicatorButton(display_date_offset=self.display_date_offset),
                NextButton(),
            ]

    @property
    @override
    def pages(self) -> "NavPages":
        """
        The pages that the navigator is navigating.
        """
        return self._pages

    @property
    @override
    def current_page(self) -> int:
        """
        The current page of the navigator, zero-indexed integer.
        """
        return self._current_page

    @current_page.setter
    def current_page(self, value: int) -> None:
        if not isinstance(value, int):
            raise TypeError("Expected type int for property current_page.")

        # Ensure this value is always correct
        self._current_page = max(
            -(self.pages.history_len - 1), min(value, self.pages.lookahead_len)
        )


class NavPages(DateRangeDict):
    """Class to maintain a dict of slash command responses over time.

    The key for the dict is the datetime after which the response was posted
    and the value is the HMessage instance for the response.
    Additionally the key also accepts an int and interprets it as n periods
    since the currrent datetime rounded down.

    __init__ registers tasks to update the dict regularly based on the
    lookahead_update_interval.

    Parameters
    channel: h.GuildNewsChannel
        The channel to fetch messages from
    period: dt.timedelta
        The period between each key
    reference_date: dt.datetime
        The date to use as the reference for the 0 key
    history_len: int
        The number of periods to keep in the past
    lookahead_len: int
        The number of periods to keep in the future
    lookahead_update_interval: int
        The number of seconds between each update of the lookahead
    suppress_content_autoembeds: bool
        Instructs the default preprocess_messages method to stop discord link auto
        embeds based on message content
    no_data_message: HMessage
        Message to use when no data is available
    """

    # Strong reference to the lookahead auto-update task, set in _setup_autoupdate
    # when lookahead_len > 0. Held only to keep the task from being garbage
    # collected; NavPages instances are process-lifetime singletons so the task is
    # not cancelled in normal operation (see teardown()).
    _lookahead_task: "Task[None] | None" = None

    # Auto-update teardown handles: the registered history-updater listener and a
    # double-setup guard. NavPages are process-lifetime singletons today, so these
    # are dormant; they let a future recreation/hot-reload path release the per-
    # instance listener + lookahead task instead of leaking them (memory-leak N4).
    _history_updater: "t.Callable[..., t.Coroutine[t.Any, t.Any, None]] | None" = None
    _autoupdate_set_up: bool = False

    def __init__(
        self,
        channel: h.GuildNewsChannel,
        period: dt.timedelta,
        reference_date: dt.datetime,
        history_len: int = 7,
        lookahead_len: int = 0,
        lookahead_update_interval: int = 1800,
        suppress_content_autoembeds: bool = True,
        no_data_message: HMessage | None = None,
    ):
        super().__init__(period)
        self.history_len = history_len
        self.lookahead_len = lookahead_len
        self.channel = channel
        self.bot: CachedFetchBot = t.cast(CachedFetchBot, channel.app)
        self.lookahead_update_interval = lookahead_update_interval

        self._reference_date = reference_date
        self._suppress_content_autoembeds = suppress_content_autoembeds
        if no_data_message is None:
            no_data_message = HMessage(embeds=[NO_DATA_HERE_EMBED])
        self.no_data_message = no_data_message

    @override
    def __getitem__(self, key: dt.datetime | int) -> HMessage:
        try:
            return super().__getitem__(key)
        except KeyError:
            return self.no_data_message

    @property
    def limits(self) -> tuple[dt.datetime, dt.datetime]:
        midpoint = self.nearest_limit_from_period_and_ref(
            period=self.period, ref=self._reference_date
        )
        limit_low = midpoint - self.period * (self.history_len - 1)
        limit_high = midpoint + self.period * self.lookahead_len
        return (limit_low, limit_high)

    def preprocess_messages(self, messages: list[h.Message]) -> HMessage:
        if not messages:
            return self.no_data_message
        msg: HMessage = accumulate([HMessage.from_message(msg) for msg in messages])

        if self._suppress_content_autoembeds:
            # Stop discord from making new auto embeds
            msg.content = (
                url_regex.sub(lambda x: f"<{x.group()}>", msg.content)
                .replace("<<", "<")
                .replace(">>", ">")
            )

        # Remove discord auto image embeds
        msg.embeds = utils.filter_discord_autoembeds(msg)
        # Remove embeds with no title or description
        msg.embeds = list(filter(lambda x: x.title or x.description, msg.embeds))

        return msg

    @classmethod
    async def from_channel(cls, bot: h.RESTAware, channel, **kwargs) -> t.Self:
        """
        Create a NavPages instance from a channel ID or channel object.

        Additional keyword arguments (kwargs) are passed directly to the class
        constructor. This allows customization of the instance at creation.

        Args:
            bot: The bot instance used to fetch the channel if needed.
            channel: The channel ID or channel object to create from.
            period (dt.timedelta): The period between each key.
            reference_date (dt.datetime): The date to use as the reference for
                the 0 key.
            history_len (int, optional): The number of periods to keep in the
                past. Default is 7.
            lookahead_len (int, optional): The number of periods to keep in the
                future. Default is 0.
            lookahead_update_interval (int, optional): The number of seconds
                between each update of the lookahead. Default is 1800.
            suppress_content_autoembeds (bool, optional): If True, instructs the
                default preprocess_messages method to stop Discord link auto
                embeds based on message content. Default is True.
            no_data_message (HMessage, optional): Message to use when no
                data is available. Default is HMessage(embeds=[NO_DATA_HERE_EMBED]).
            **kwargs: Additional keyword arguments for the class constructor.

        Returns:
            An instance of NavPages.
        """
        if isinstance(channel, (int, h.Snowflake)):
            channel = await t.cast(CachedFetchBot, bot).fetch_channel(int(channel))

        if not isinstance(channel, h.GuildNewsChannel):
            raise TypeError(
                f"Cannot create {cls.__name__} from {channel.__class__.__name__} "
                + "since it is not an Announce channel"
            )

        self: t.Self = cls(channel, **kwargs)

        await self._populate_history()
        await self._update_lookahead()
        self._setup_autoupdate()

        return self

    async def _populate_history(self):
        # Find start time
        after = self.limits[0]

        # Bin messages into periods
        binned_messages: dict[dt.datetime, list[h.Message]] = {}
        async for msg in self.channel.fetch_history(after=after - reset_time_tolerance):
            start_of_period = self.round_down(msg.timestamp)
            binned_messages.setdefault(start_of_period, []).append(msg)

        # Preprocess messages
        key = self.limits[0]
        now = dt.datetime.now(tz=dt.UTC)
        while key <= now:
            if binned_messages.get(key):
                self[key] = self.preprocess_messages(binned_messages[key])
            key += self.period

    @utils.ignore_own_user
    async def _update_history(self, event: h.MessageCreateEvent | h.MessageUpdateEvent):
        """Updates the history with any changes or new messages in self.channel"""

        if event.channel_id != self.channel.id:
            return

        logging.info(
            ("Update " if isinstance(event, h.MessageUpdateEvent) else "Create ")
            + f"event received in channel id {event.channel_id} "
            + f"for message id {event.message_id}"
        )

        retries = 12
        for retry_no in range(retries):
            try:
                if isinstance(event.message, h.Message):
                    msg = event.message
                elif isinstance(event.message, h.PartialMessage):
                    msg = await self.bot.fetch_message(
                        event.channel_id, event.message_id
                    )
                elif isinstance(event.message, h.Snowflakeish):
                    msg = await self.bot.fetch_message(event.channel_id, event.message)
                else:
                    raise ValueError(f"Unknown message type {event.message.__class__}")

                if not (
                    self.limits[0] <= self.round_down(msg.timestamp) <= self.limits[1]
                ):
                    logging.info(
                        f"Message {msg.id} not in limits {self.limits}. Ignoring"
                    )
                    return

                # Get all messages in this event's message's period
                from_ = self.round_down(msg.timestamp)
                until_ = from_ + self.period
                msgs_from_api = []
                async for msg_from_api in self.channel.fetch_history(after=from_):
                    if msg_from_api.timestamp > until_:
                        break
                    msgs_from_api.append(msg_from_api)

                self[from_] = self.preprocess_messages(msgs_from_api)

            except Exception as e:
                await discord_error_logger(e, operation="Nav backfill")
                await sleep(2**retry_no)
            else:
                break

    async def _update_lookahead(self):
        if self.lookahead_len <= 0:
            return

        self.update(
            await self.lookahead(
                self.index_to_date(1, tolerance=dt.timedelta(minutes=1))
            )
        )

    def _setup_autoupdate(self):
        if self._autoupdate_set_up:
            return
        self._autoupdate_set_up = True
        if self.history_len > 0:

            @self.bot.listen()
            async def history_updater(
                event: h.MessageCreateEvent
                | h.MessageUpdateEvent
                | h.MessageDeleteEvent,
            ):
                if isinstance(event, h.MessageDeleteEvent):
                    if event.channel_id == self.channel.id:
                        self.purge_history()
                        await self._populate_history()
                else:
                    await self._update_history(event)

            self._history_updater = history_updater

        if self.lookahead_len > 0:
            # Lightbulb v3 removed lightbulb.ext.tasks, and this updater is created
            # per-NavPages-instance (not at module load) so it cannot use the loader's
            # task registry. Self-schedule it with an asyncio loop instead.
            async def lookahead_update_task():
                while True:
                    await sleep(self.lookahead_update_interval)
                    try:
                        # Introduce a 5% jitter to the update interval
                        # to avoid ratelimit issues
                        await sleep(
                            randint(0, int(self.lookahead_update_interval / 20))
                        )
                        await self._update_lookahead()
                    except Exception as e:
                        await discord_error_logger(e, operation="Nav lookahead")

            # Keep a strong reference: the event loop only holds a weak ref to a
            # bare task, so without this the updater can be garbage-collected
            # mid-flight and silently stop.
            self._lookahead_task = create_task(lookahead_update_task())

    def teardown(self) -> None:
        """Release the auto-update listener + lookahead task.

        Unsubscribes the history-updater from all three message events and cancels
        the lookahead task. NavPages are created once per followable at startup, so
        nothing accumulates in normal operation; this exists so a future
        recreation/hot-reload path can avoid leaking them (memory-leak N4).
        """
        if self._history_updater is not None:
            for event_type in (
                h.MessageCreateEvent,
                h.MessageUpdateEvent,
                h.MessageDeleteEvent,
            ):
                self.bot.unsubscribe(event_type, self._history_updater)
            self._history_updater = None
        if self._lookahead_task is not None:
            self._lookahead_task.cancel()
            self._lookahead_task = None
        self._autoupdate_set_up = False

    async def lookahead(self, after: dt.datetime) -> dict[dt.datetime, HMessage]:
        """Return the predicted messages for the periods after <after>

        The dict must have <self.lookahead_len> entries, indexed by the start of the
        period and must contain the HMessage for that period."""
        return {}


class IndicatorButton(nav.IndicatorButton):
    """
    A built-in NavButton to indicate the current page.
    """

    def __init__(
        self,
        *,
        custom_id: str | None = None,
        emoji: h.Emoji | str | None = None,
        row: int | None = None,
        display_date_offset: dt.timedelta = dt.timedelta(days=0),
    ):
        super().__init__(
            style=h.ButtonStyle.SECONDARY, custom_id=custom_id, emoji=emoji, row=row
        )
        self.display_date_offset = display_date_offset

    @override
    async def callback(self, context: m.ViewContext) -> None:
        pass

    @override
    async def before_page_change(self) -> None:
        view = t.cast(NavigatorView, self.view)
        date = view.pages.index_to_date(view.current_page)
        date += self.display_date_offset
        suffix = get_ordinal_suffix(date.day)
        self.label = f"{date.strftime('%B %-d')}{suffix}"


class NextButton(nav.NavButton):
    """
    A built-in NavButton to jump to the next page.
    """

    def __init__(
        self,
        *,
        style: h.ButtonStyle = h.ButtonStyle.PRIMARY,
        label: str | None = None,
        custom_id: str | None = None,
        emoji: h.Emoji | str | None = NEXT_PAGE_EMOJI,
        row: int | None = None,
    ):
        super().__init__(
            style=style, label=label, custom_id=custom_id, emoji=emoji, row=row
        )

    @override
    async def callback(self, context: m.ViewContext) -> None:
        self.view.current_page += 1
        await self.view.send_page(context)

    @override
    async def before_page_change(self) -> None:
        view = t.cast(NavigatorView, self.view)
        self.disabled = view.current_page >= view.pages.lookahead_len


class PrevButton(nav.NavButton):
    """
    A built-in NavButton to jump to previous page.
    """

    def __init__(
        self,
        *,
        style: h.ButtonStyle = h.ButtonStyle.PRIMARY,
        label: str | None = None,
        custom_id: str | None = None,
        emoji: h.Emoji | str | None = PREV_PAGE_EMOJI,
        row: int | None = None,
    ):
        super().__init__(
            style=style, label=label, custom_id=custom_id, emoji=emoji, row=row
        )

    @override
    async def callback(self, context: m.ViewContext) -> None:
        self.view.current_page -= 1
        await self.view.send_page(context)

    @override
    async def before_page_change(self) -> None:
        view = t.cast(NavigatorView, self.view)
        self.disabled = view.current_page <= 1 - view.pages.history_len


# Regex matching the "**From**"/"**Till**" lines that the anchor bot adds to
# reset/gunsmith posts; these are stripped from the mirrored embed.
rgx_find_from_till_text = re.compile(r"\n\*\*(From|Till)\*\*[^\n]*")


class ResetPages(NavPages):
    """NavPages for posts that share the weekly-reset anchor formatting.

    Both the weekly-reset and gunsmith posts use identical preprocessing, so
    they share this subclass: merge the message content and attachments into the
    embed and strip the redundant From/Till lines.
    """

    @override
    def preprocess_messages(self, messages: list[h.Message]) -> HMessage:
        if not messages:
            return self.no_data_message
        for message in messages:
            message.embeds = utils.filter_discord_autoembeds(message)
        msg_proto = (
            accumulate([HMessage.from_message(message) for message in messages])
            .merge_content_into_embed()
            .merge_attachements_into_embed(default_url=default_url)
        )

        # Remove duplicate From/Till text from anchor embed
        for embed in msg_proto.embeds:
            embed.description = rgx_find_from_till_text.sub("", embed.description or "")

        return msg_proto


class NavPagesHolder:
    """Late-bound container for a :class:`NavPages` built on ``StartedEvent``.

    The pages object can only be built once the bot has started (it reads
    channel history), but command callbacks need it at invoke time. The holder
    lets a shared ``StartedEvent`` listener populate ``.pages`` while commands
    close over the holder and read ``.pages`` lazily.
    """

    def __init__(self) -> None:
        self.pages: NavPages | None = None


def setup_nav_pages(
    loader: lb.Loader,
    *,
    followable_channel: int,
    pages_cls: type[NavPages] = NavPages,
    **from_channel_kwargs: t.Any,
) -> NavPagesHolder:
    """Register a ``StartedEvent`` listener that builds the pages into a holder.

    ``pages_cls`` is built from ``followable_channel`` once the bot starts.
    Extra keyword arguments are forwarded to :meth:`NavPages.from_channel`
    (``period``, ``reference_date``, ``history_len``, ``lookahead_len``,
    ``suppress_content_autoembeds``, ``no_data_message`` ...).
    """
    holder = NavPagesHolder()

    @loader.listener(h.StartedEvent)
    async def _on_start(event: h.StartedEvent) -> None:
        holder.pages = await pages_cls.from_channel(
            event.app, followable_channel, **from_channel_kwargs
        )

    return holder


def make_navigator_command(
    holder: NavPagesHolder,
    *,
    name: str,
    description: str,
    autodefer: bool = True,
    allow_start_on_blank_page: bool = False,
    display_date_offset: dt.timedelta = dt.timedelta(days=0),
) -> type[lb.SlashCommand]:
    """Build a SlashCommand that shows ``holder.pages`` in a NavigatorView.

    The returned class is *not* registered; the caller registers it with
    ``loader.command(...)`` or ``group.register(...)`` as appropriate.
    """

    class _NavCommand(lb.SlashCommand, name=name, description=description):
        @lb.invoke
        async def invoke(self, ctx: lb.Context):
            if holder.pages is None:
                raise RuntimeError(f"Navigator pages for '{name}' not yet initialised")
            navigator = NavigatorView(
                pages=holder.pages,
                autodefer=autodefer,
                allow_start_on_blank_page=allow_start_on_blank_page,
                display_date_offset=display_date_offset,
            )
            await navigator.send(ctx.interaction)

    return _NavCommand
