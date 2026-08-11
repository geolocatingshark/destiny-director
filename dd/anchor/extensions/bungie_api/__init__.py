"""Bungie.net API integration for the anchor bot.

OAuth token management and the authenticated vendor/profile API calls used to build the
Xûr and Eververse posts. Manifest *data* no longer lives here at all — it is a typed
projection in Postgres, written by ``dd.manifest_ingest`` and read through
``.manifest_db`` / ``.item_index``.

This package is the discovered lightbulb extension: it owns ``loader`` and the
``/bungie`` command group, and re-exports the public surface (models, OAuth helpers,
constants) so importers keep using
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


# There is no manifest prewarm here any more, and there must not be one again.
#
# The manifest used to be downloaded and extracted inside this process — hundreds of MB
# and a multi-minute extract — so a boot-time prewarm existed to keep the first request
# from wearing that cost. The manifest now lives in Postgres, written by the out-of-band
# `dd.manifest_ingest` cron, and every consumer reads it with indexed queries. There is
# nothing to warm, nothing to coalesce concurrent resolves onto, and no cold window.
