# Copyright © 2019-present gsfernandes81

# This file is part of "destiny-director".

# destiny-director is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later version.

# "destiny-director" is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License along with
# destiny-director. If not, see <https://www.gnu.org/licenses/>.

import datetime as dt
import json
import logging
import ssl
import typing as t
from os import getenv as __getenv

import hikari as h
import regex as re
from sqlalchemy.ext.asyncio import AsyncSession


def _getenv(
    var_name: str,
    default: t.Optional[str] = None,
    *,
    optional: bool = False,
    cast_to: t.Type[t.Any] = str,
) -> str:
    var = __getenv(var_name)
    if var is not None:
        return cast_to(var)
    elif default is not None:
        return default
    elif optional:
        return None
    else:
        raise ValueError(f"Environment variable {var_name} not set")


def _test_env(var_name: str) -> list[int] | bool:
    test_env = _getenv(var_name, default="false")
    test_env = test_env.lower()
    test_env = (
        [int(env.strip()) for env in test_env.split(",")]
        if test_env != "false"
        else False
    )
    return test_env


def lightbulb_params(
    include_message_content_intent: bool,
    central_guilds_only: bool,
    discord_token: str,
) -> dict:
    """
    Returns configuration parameters for lightbulb code within the bot

    Args:
        include_message_content_intent (bool): Whether the bot should receive message
        contents from the api

        central_guilds_only (bool): Whether the bot should only be enabled in the
        central servers
    """
    intents = h.Intents.ALL_UNPRIVILEGED

    if include_message_content_intent:
        intents = intents | h.Intents.MESSAGE_CONTENT

    lightbulb_params = {
        "token": discord_token,
        "intents": intents,
        "max_rate_limit": 600,
    }
    # Only use the test env for testing if it is specified
    if test_env:
        lightbulb_params["default_enabled_guilds"] = test_env
    elif central_guilds_only:
        lightbulb_params["default_enabled_guilds"] = [
            kyber_discord_server_id,
            control_discord_server_id,
        ]
    return lightbulb_params


def _db_urls(var_name: str, var_name_alternative) -> tuple[str, str]:
    try:
        db_url = _getenv(var_name)
    except ValueError:
        db_url = _getenv(var_name_alternative)

    __repl_till = db_url.find("://")
    db_url = db_url[__repl_till:]
    db_url_async = "mysql+asyncmy" + db_url
    db_url = "mysql" + db_url
    return db_url, db_url_async


def _db_config():
    db_session_kwargs_sync = {
        "expire_on_commit": False,
    }
    db_session_kwargs = db_session_kwargs_sync | {
        "class_": AsyncSession,
    }

    db_connect_args = {}
    if _getenv("MYSQL_SSL", "true") == "true":
        ssl_ctx = ssl.create_default_context(
            cafile="/etc/ssl/certs/ca-certificates.crt"
        )
        ssl_ctx.verify_mode = ssl.CERT_REQUIRED
        db_connect_args.update({"ssl": ssl_ctx})

    db_engine_args = {
        "max_overflow": -1,
        "isolation_level": "READ COMMITTED",
        "pool_pre_ping": True,
        "pool_recycle": 3600,
    }
    return db_session_kwargs, db_session_kwargs_sync, db_connect_args, db_engine_args


def _sheets_credentials(
    proj_id: str,
    priv_key_id: str,
    priv_key: str,
    client_email: str,
    client_id: str,
    client_x509_cert_url: str,
) -> dict[str, str]:
    gsheets_credentials = {
        "type": "service_account",
        "project_id": _getenv(proj_id),
        "private_key_id": _getenv(priv_key_id),
        "private_key": _getenv(priv_key).replace("\\n", "\n"),
        "client_email": _getenv(client_email),
        "client_id": _getenv(client_id),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": _getenv(client_x509_cert_url),
    }
    return gsheets_credentials


######### loglevel config #########

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname).1s %(name)s | %(message)s",
)
###### Environment variables ######

# Discord environment config
test_env = _test_env("TEST_ENV")
discord_token_anchor = _getenv("DISCORD_TOKEN_ANCHOR", optional=True)
discord_token_beacon = _getenv("DISCORD_TOKEN_BEACON", optional=True)
disable_bad_channels = (
    _getenv("DISABLE_BAD_CHANNELS", default="", optional=True).lower() == "true"
)

# Discord control server config
control_discord_server_id = _getenv("CONTROL_DISCORD_SERVER_ID", cast_to=int)
control_discord_role_id = _getenv("CONTROL_DISCORD_ROLE_ID", optional=True, cast_to=int)
admins = [
    int(admin.strip())
    for admin in _getenv("ADMINS", default="", optional=True).split(",")
]
kyber_discord_server_id = _getenv("KYBER_DISCORD_SERVER_ID", cast_to=int)
log_channel = _getenv("LOG_CHANNEL_ID", cast_to=int)
alerts_channel = _getenv("ALERTS_CHANNEL_ID", cast_to=int)


# Discord constants
embed_default_color = h.Color(int(_getenv("EMBED_DEFAULT_COLOR"), 16))
embed_error_color = h.Color(int(_getenv("EMBED_ERROR_COLOR"), 16))
followables: t.Dict[str, int] = json.loads(_getenv("FOLLOWABLES"), parse_int=int)
default_url = _getenv("DEFAULT_URL", optional=True)
navigator_timeout = _getenv("NAVIGATOR_TIMEOUT", optional=True, cast_to=int) or 120

# Database URLs
db_url, db_url_async = _db_urls("MYSQL_PRIVATE_URL", "MYSQL_URL")

# Sheets credentials & URLs
gsheets_credentials = _sheets_credentials(
    "SHEETS_PROJECT_ID",
    "SHEETS_PRIVATE_KEY_ID",
    "SHEETS_PRIVATE_KEY",
    "SHEETS_CLIENT_EMAIL",
    "SHEETS_CLIENT_ID",
    "SHEETS_CLIENT_X509_CERT_URL",
)
sheets_ls_url = _getenv("SHEETS_LS_URL")

# Bungie credentials
bungie_api_key = _getenv("BUNGIE_API_KEY", optional=True)
bungie_client_id = _getenv("BUNGIE_CLIENT_ID", optional=True)
bungie_client_secret = _getenv("BUNGIE_CLIENT_SECRET", optional=True)


port = _getenv("PORT", 8080, cast_to=int)
#### Environment variables end ####

###################################

####### Configs & constants #######

(
    db_session_kwargs,
    db_session_kwargs_sync,
    db_connect_args,
    db_engine_args,
) = _db_config()

reset_time_tolerance = dt.timedelta(minutes=60)
url_regex = re.compile(
    "http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"
)
IMAGE_EXTENSIONS_LIST = [
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".tiff",
    ".tif",
    ".heif",
    ".heifs",
    ".heic",
    ".heics",
    ".webp",
]

##### Configs & constants end #####
