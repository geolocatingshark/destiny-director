"""Bungie.net API integration for the anchor bot.

Handles the Destiny 2 manifest download/caching, OAuth token management, and the
authenticated vendor/profile API calls used to build the Xûr and Eververse posts.

This package is the discovered lightbulb extension: it owns ``loader`` and the
``/bungie`` command group, and re-exports the public surface (models, OAuth helpers,
manifest helpers, constants) so importers keep using
``dd.anchor.extensions.bungie_api.<symbol>`` unchanged.
"""

import aiohttp
import lightbulb as lb

from dd.anchor import web
from dd.common.components import cv2_error, cv2_notice, cv2_success, respond_cv2

from . import client
from .constants import (
    ADA_VENDOR_HASH,
    ARMOR_TYPE_NAMES,
    DESTINY_CLASS_TYPE_IDS,
    DESTINY_CLASSES_ENUM,
    EVERVERSE_BRIGHT_DUST_ROTATOR_PREFIX,
    EVERVERSE_SILVER_ROTATOR_PREFIX,
    VENDOR_NOT_FOUND_ERROR_CODE,
    XUR_STRANGE_GEAR_VENDOR_HASH,
    XUR_VENDOR_HASH,
    likely_emoji_name,
)
from .manifest import _build_manifest_dict, _get_latest_manifest
from .models import (
    APIOffline,
    DestinyArmor,
    DestinyCollectible,
    DestinyItem,
    DestinyMembership,
    DestinyPresentationNode,
    DestinyVendor,
    DestinyWeapon,
    VendorNotFound,
)
from .oauth import (
    APIOfflineException,
    OAuthStateManager,
    check_bungie_api_online,
    get_webserver_runner,
    oauth_url,
    refresh_api_tokens,
    register_oauth_routes,
    webserver_runner_preparation,
)

__all__ = [
    "client",
    "ADA_VENDOR_HASH",
    "ARMOR_TYPE_NAMES",
    "DESTINY_CLASSES_ENUM",
    "DESTINY_CLASS_TYPE_IDS",
    "EVERVERSE_BRIGHT_DUST_ROTATOR_PREFIX",
    "EVERVERSE_SILVER_ROTATOR_PREFIX",
    "VENDOR_NOT_FOUND_ERROR_CODE",
    "XUR_STRANGE_GEAR_VENDOR_HASH",
    "XUR_VENDOR_HASH",
    "likely_emoji_name",
    "_build_manifest_dict",
    "_get_latest_manifest",
    "APIOffline",
    "APIOfflineException",
    "DestinyArmor",
    "DestinyCollectible",
    "DestinyItem",
    "DestinyMembership",
    "DestinyPresentationNode",
    "DestinyVendor",
    "DestinyWeapon",
    "VendorNotFound",
    "OAuthStateManager",
    "check_bungie_api_online",
    "get_webserver_runner",
    "oauth_url",
    "refresh_api_tokens",
    "register_oauth_routes",
    "webserver_runner_preparation",
    "loader",
    "bungie",
]

# Serve the Bungie OAuth callback from the anchor's persistent web app (replaces the
# transient per-/bungie-login server). Registered at extension-import time, before the
# gateway reaches StartedEvent where the web app is built and started.
web.register_routes(register_oauth_routes)


loader = lb.Loader()

bungie = lb.Group("bungie", "Bungie API related commands")


@bungie.register
class Login(
    lb.SlashCommand,
    name="login",
    description="Log in to the app with a Bungie account",
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context):
        initial = await respond_cv2(
            ctx, cv2_notice(f"Please log in at {oauth_url()}"), ephemeral=True
        )
        try:
            await refresh_api_tokens(runner=get_webserver_runner(), with_login=True)
        except TimeoutError:
            await ctx.edit_response(
                initial,
                components=[
                    cv2_error(
                        "Login timed out",
                        "Timed out after 15 minutes. Run `/bungie login` again.",
                    )
                ],
            )
            return
        await ctx.edit_response(
            initial, components=[cv2_success("Successfully logged in")]
        )


@bungie.register
class AccountNumbers(
    lb.SlashCommand,
    name="account_numbers",
    description="Get the character id, destiny membership id and membership type",
):
    @lb.invoke
    async def invoke(self, ctx: lb.Context):
        # Ack within Discord's 3s window with a placeholder, then edit in the result;
        # the token refresh + Bungie round-trips below take longer than 3s.
        initial = await respond_cv2(
            ctx, cv2_notice("Fetching account numbers…"), ephemeral=True
        )
        access_token = await refresh_api_tokens(runner=get_webserver_runner())

        async with aiohttp.ClientSession() as session:
            destiny_membership = await DestinyMembership.from_api(session, access_token)
            character_id = await destiny_membership.get_character_id(
                session, access_token
            )

        # Note: the OAuth access token is intentionally not included here. It is a
        # live credential and must never be surfaced in a Discord message, even an
        # ephemeral one.
        await ctx.edit_response(
            initial,
            components=[
                cv2_notice(
                    "```\n"
                    f"Destiny Character ID: {character_id}\n"
                    f"Destiny Membership ID: {destiny_membership.membership_id}\n"
                    f"Destiny Membership Type: {destiny_membership.membership_type}"
                    "\n```"
                )
            ],
        )


# No guilds= → inherits the client's default_enabled_guilds (control + test_env); the
# client-level owner hook gates these Bungie-credential commands to bot owners.
loader.command(bungie)
