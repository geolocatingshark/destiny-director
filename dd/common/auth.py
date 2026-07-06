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

"""Shared owner-authorization primitives for both bots.

A single bot-owner gate, expressed three ways so it can be applied uniformly:

- :func:`check_invoker_is_owner` — the predicate (used directly where a command needs
  ``owner OR <something else>``, e.g. beacon autoposts' perms model).
- :data:`owner_only` — a CHECKS-step hook attached via ``hooks=[owner_only]`` (beacon's
  per-command gate) or passed to ``client_from_app(..., hooks=[owner_only])`` to gate
  *every* command on a client (anchor, which is owner-only in its entirety).
- :func:`owner_check_error_handler` — a client-level error handler that turns the hook's
  :class:`NotBotOwnerError` into an ephemeral rejection, registered ahead of the
  catch-all alert reporter so owner rejections never page the alerts channel.
"""

import typing as t

import lightbulb as lb

from .bot import CachedFetchBot
from .components import cv2_error, respond_cv2


class NotBotOwnerError(Exception):
    """Raised by :data:`owner_only` when a non-owner invokes a gated command.

    Rendered by :func:`owner_check_error_handler`."""


async def check_invoker_is_owner(ctx: lb.Context) -> bool:
    """Return whether the command's invoker is one of the bot's owners."""
    bot = t.cast(CachedFetchBot, ctx.client.app)
    return ctx.user.id in await bot.fetch_owner_ids()


@lb.hook(lb.ExecutionSteps.CHECKS)
async def owner_only(_pl: lb.ExecutionPipeline, ctx: lb.Context) -> None:
    """CHECKS-step hook gating a command to bot owners.

    Raising fails the pipeline before the command is invoked; the failure is
    rendered by :func:`owner_check_error_handler`. Attach via ``hooks=[owner_only]``
    on a command, or pass to ``client_from_app(..., hooks=[owner_only])`` to gate
    every command on a client.
    """
    if not await check_invoker_is_owner(ctx):
        raise NotBotOwnerError


async def owner_check_error_handler(
    exc: lb.exceptions.ExecutionPipelineFailedException,
) -> bool:
    """Render a :class:`NotBotOwnerError` rejection ephemerally; pass others on.

    Register on each client (``client.error_handler(owner_check_error_handler)``) at the
    default priority so it runs before the low-priority catch-all alert reporter — owner
    rejections are routine and must not reach the alerts channel.
    """
    cause = exc.causes[0] if exc.causes else exc
    if isinstance(cause, NotBotOwnerError):
        await respond_cv2(
            exc.context,
            cv2_error("You are not authorized to use this command."),
            ephemeral=True,
        )
        return True
    return False
