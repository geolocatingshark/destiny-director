import hikari as h
import lightbulb as lb

from dd.common import cfg
from dd.common.utils import update_status

loader = lb.Loader()


class GuildCounter:
    def __init__(self):
        self._guild_count = 0

    def __int__(self) -> int:
        return self._guild_count

    def __str__(self) -> str:
        return f"Guild Counter ({self._guild_count})"

    def increment(self) -> None:
        self._guild_count += 1

    def decrement(self) -> None:
        self._guild_count -= 1

    def set(self, count: int) -> None:
        self._guild_count = count


class GuildCounterDILoadable(lb.Loadable):
    def __init__(self):
        self.guild_counter = GuildCounter()

    async def load(self, client: lb.Client) -> None:
        registry = client.di.registry_for(lb.di.Contexts.DEFAULT)
        registry.register_value(GuildCounter, self.guild_counter)

    async def unload(self, client: lb.Client) -> None:
        # Unloading not supported
        pass


@loader.listener(h.StartedEvent)
async def on_start(
    event: h.StartedEvent,
    bot: h.GatewayBot = lb.di.INJECTED,
    guild_counter: GuildCounter = lb.di.INJECTED,
) -> None:
    guild_counter._guild_count = len(await bot.rest.fetch_my_guilds())
    await update_status(bot, int(guild_counter), bool(cfg.test_env))


@loader.listener(h.GuildJoinEvent)
async def on_guild_join(
    event: h.GuildJoinEvent,
    bot: h.GatewayBot = lb.di.INJECTED,
    guild_counter: GuildCounter = lb.di.INJECTED,
) -> None:
    guild_counter.increment()
    await update_status(bot, int(guild_counter), bool(cfg.test_env))


@loader.listener(h.GuildLeaveEvent)
async def on_guild_leave(
    event: h.GuildLeaveEvent,
    bot: h.GatewayBot = lb.di.INJECTED,
    guild_counter: GuildCounter = lb.di.INJECTED,
) -> None:
    guild_counter.decrement()
    await update_status(bot, int(guild_counter), bool(cfg.test_env))
