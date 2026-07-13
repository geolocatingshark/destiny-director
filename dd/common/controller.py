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

"""Shared bot-administration command group (stop / restart / info) for both bots.

Factory mirroring ``make_source_command``: each call builds a *fresh* group named
after the bot (``/anchor`` or ``/beacon``). Lightbulb command objects carry per-client
registration state, so a fresh group is built per call, not shared across clients. The
factory applies ``owner_only`` to each subcommand itself rather than relying on a
client-wide gate (anchor gates its whole client, beacon does not); harmless on anchor.
The wrappers scope registration to the control guild.

Beacon passes a ``mirror_check`` so stop/restart warn and require a DANGER override
while mirror operations are in progress. Termination goes through
:mod:`dd.common.lifecycle` (schedule ``close`` + exit on the main thread) so it works
from a button callback too: a raw ``sys.exit`` in a component callback is swallowed by
hikari's fire-and-forget task wrapper.

``stop`` exits cleanly (code 0) and only stops a service whose restart policy is not
``ALWAYS``. All services are ``ON_FAILURE`` (prod beacon was flipped from ``ALWAYS`` on
2026-06-25), so ``/beacon stop`` works everywhere. ``restart`` exits non-zero and works
under any restart-on-failure policy.

``restart`` is **disabled in prod** (see :func:`restarts_enabled`): a non-zero exit is a
crash to Railway, and Railway applies crash-loop backoff, so repeated ``/restart`` there
risks leaving the bot down. In prod the command refuses and takes no action; operators
redeploy from Railway instead. It stays available in dev/test.
"""

import asyncio
import contextlib
import typing as t

import hikari as h
import lightbulb as lb
from lightbulb import components as lbc

from . import cfg, lifecycle
from .auth import owner_only
from .bot import CachedFetchBot
from .components import (
    CV2_DANGER_COLOR,
    CV2_NEUTRAL_COLOR,
    CV2_WARNING_COLOR,
    build_container,
    cv2_notice,
    respond_cv2,
)
from .schemas import MirroredChannel


def restarts_enabled() -> bool:
    """Whether ``/restart`` may exit non-zero to trigger a Railway restart.

    Disabled in prod (``cfg.test_env`` falsy — an empty tuple): Railway reads a non-zero
    exit as a crash and applies crash-loop backoff, so a ``/restart`` there can trip
    that backoff and leave the bot down. In prod, restart via redeploy instead. Enabled
    in dev/test, where ``TEST_ENV`` is set and the exit-and-be-restarted trick is safe.
    """
    return bool(cfg.test_env)


async def _run_lifecycle(
    ctx: lb.Context,
    bot: CachedFetchBot,
    *,
    exit_code: int,
    action: str,
    verb: str,
    mirror_check: t.Callable[[], t.Awaitable[int]] | None,
) -> None:
    """Stop/restart the bot; warn + require a DANGER override if mirrors are live."""
    n = await mirror_check() if mirror_check is not None else 0
    if n == 0:
        await respond_cv2(ctx, cv2_notice(f"Bot is {action} now."), ephemeral=True)
        await lifecycle.request_shutdown(bot, exit_code)
        return

    # Mirror-in-progress override flow (beacon only — anchor passes mirror_check=None,
    # so n is always 0 above). Left as an embed + menu pending the deferred beacon CV2
    # pass; converting an interactive embed+menu to CV2 is out of scope here. Its accent
    # colours now come from the shared CV2_* constants so there's one palette repo-wide.
    decided = False

    async def on_confirm(mctx: lbc.MenuContext) -> None:
        nonlocal decided
        if mctx.user.id not in await bot.fetch_owner_ids():
            await mctx.respond("You are not authorized.", ephemeral=True)
            return
        decided = True
        await mctx.respond(
            edit=True,
            embed=h.Embed(description=f"Bot is {action} now.", color=CV2_DANGER_COLOR),
            components=[],
        )
        await lifecycle.request_shutdown(bot, exit_code)
        mctx.stop_interacting()

    async def on_cancel(mctx: lbc.MenuContext) -> None:
        nonlocal decided
        if mctx.user.id not in await bot.fetch_owner_ids():
            await mctx.respond("You are not authorized.", ephemeral=True)
            return
        decided = True
        await mctx.respond(
            edit=True,
            embed=h.Embed(
                description="Aborted — no action taken.", color=CV2_NEUTRAL_COLOR
            ),
            components=[],
        )
        mctx.stop_interacting()

    menu = lbc.Menu()
    menu.add_interactive_button(
        h.ButtonStyle.DANGER,
        on_confirm,
        custom_id=f"dd_lifecycle_go:{ctx.interaction.id}",
        label=f"{verb} now",
    )
    menu.add_interactive_button(
        h.ButtonStyle.SECONDARY,
        on_cancel,
        custom_id=f"dd_lifecycle_no:{ctx.interaction.id}",
        label="Cancel",
    )

    await ctx.respond(
        embed=h.Embed(
            title="⚠️ Mirrors in progress",
            description=(
                f"{n} mirror operation(s) are still running. {action.capitalize()} now "
                "will interrupt them — already-sent destinations are recorded and the "
                "rest reconcile on the next run. Wait for them to finish, or override."
            ),
            color=CV2_WARNING_COLOR,
        ),
        components=menu,
        ephemeral=True,
    )

    with contextlib.suppress(TimeoutError):
        await menu.attach(ctx.client, timeout=60)
    if not decided:
        # Timed out without a choice — disable the (now stale) buttons.
        await ctx.interaction.edit_initial_response(
            embed=h.Embed(
                description="Timed out — no action taken.", color=CV2_NEUTRAL_COLOR
            ),
            components=[],
        )


def make_controller_group(
    bot_name: str,
    *,
    mirror_check: t.Callable[[], t.Awaitable[int]] | None = None,
    show_followables: bool = False,
) -> lb.Group:
    """Build a fresh bot-administration group named after ``bot_name``.

    Args:
        bot_name: The group name / top-level command, e.g. ``"anchor"`` or ``"beacon"``
            (yields ``/anchor restart`` etc.).
        mirror_check: Optional callable returning the number of in-progress mirror
            operations. When it returns > 0, stop/restart warn and require a DANGER
            override. Beacon supplies this; anchor (no mirrors) leaves it ``None``. It
            also gates ``info``'s mirror-status block (present iff this is set).
        show_followables: When ``True``, ``info`` lists every followable name → its
            announce channel (as a ``<#id>`` mention). Anchor (the poster) sets this;
            beacon shows per-followable mirror-dest counts instead.
    """
    group = lb.Group(bot_name, "Bot administration")

    @group.register
    class Restart(
        lb.SlashCommand,
        name="restart",
        description="Restart the bot",
        hooks=[owner_only],
    ):
        @lb.invoke
        async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
            # In prod, restart-by-exit is unsafe: Railway crash-loop-backs-off repeated
            # non-zero exits and can leave the bot down. Refuse and take no action —
            # the running process is left untouched; redeploy from Railway to restart.
            if not restarts_enabled():
                await respond_cv2(
                    ctx,
                    cv2_notice(
                        "Restart is disabled in production. A `/restart` exits the "
                        "process non-zero to be restarted, but Railway rate-limits "
                        "crash-looping services and may leave the bot down. Redeploy "
                        "from Railway to restart instead."
                    ),
                    ephemeral=True,
                )
                return
            await _run_lifecycle(
                ctx,
                bot,
                exit_code=lifecycle.RESTART_EXIT_CODE,
                action="restarting",
                verb="Restart",
                mirror_check=mirror_check,
            )

    @group.register
    class Stop(
        lb.SlashCommand,
        name="stop",
        description="Shut down the bot",
        hooks=[owner_only],
    ):
        @lb.invoke
        async def invoke(self, ctx: lb.Context, bot: CachedFetchBot = lb.di.INJECTED):
            await _run_lifecycle(
                ctx,
                bot,
                exit_code=lifecycle.STOP_EXIT_CODE,
                action="shutting down",
                verb="Shut down",
                mirror_check=mirror_check,
            )

    @group.register
    class Info(
        lb.SlashCommand,
        name="info",
        description="Configuration state info",
        hooks=[owner_only],
    ):
        @lb.invoke
        async def invoke(self, ctx: lb.Context):
            lines = [
                f"**Configuration Info — {bot_name}**",
                f"- Control Discord Server ID: {cfg.control_discord_server_id}",
                f"- Test Environment: {cfg.test_env}",
            ]

            if show_followables:
                lines.append("\n**Followables**")
                if cfg.followables:
                    for name, channel_id in cfg.followables.items():
                        link = f"<#{channel_id}>" if channel_id else "*(not set)*"
                        lines.append(f"- `{name}` → {link}")
                else:
                    lines.append("*(none configured)*")

            if mirror_check is not None:
                lines.append("\n**Mirror status**")
                lines.append(f"- Operations in progress: {await mirror_check()}")
                followed = [(n, c) for n, c in cfg.followables.items() if c]
                counts = await asyncio.gather(
                    *(
                        MirroredChannel.count_dests(c, legacy_only=None)
                        for _, c in followed
                    )
                )
                for (name, _), n in zip(followed, counts, strict=True):
                    lines.append(f"- `{name}` → {n} mirror dest(s)")

            await respond_cv2(ctx, build_container(["\n".join(lines)]))

    return group
