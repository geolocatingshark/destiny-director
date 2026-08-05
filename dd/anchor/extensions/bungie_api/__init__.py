"""Bungie.net API integration for the anchor bot.

Handles the Destiny 2 manifest download/caching, OAuth token management, and the
authenticated vendor/profile API calls used to build the Xûr and Eververse posts.

This package is the discovered lightbulb extension: it owns ``loader`` and the
``/bungie`` command group, and re-exports the public surface (models, OAuth helpers,
manifest helpers, constants) so importers keep using
``dd.anchor.extensions.bungie_api.<symbol>`` unchanged.
"""

import lightbulb as lb

from dd.anchor import web

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
]

# Serve the Bungie OAuth callback from the anchor's persistent web app (replaces the
# transient per-/bungie-login server). Registered at extension-import time, before the
# gateway reaches StartedEvent where the web app is built and started.
web.register_routes(register_oauth_routes)


loader = lb.Loader()

# No commands live here any more: `/bungie login` and `/bungie account_numbers` moved to
# the web control panel (dd/anchor/extensions/bungie_account.py, `/bungie`). Login in
# particular was a poor fit for Discord — it printed a URL and then blocked for up to 15
# minutes polling for the token, where on the web the redirect back IS the completion
# signal. The loader stays because load_extensions_strict requires one.
