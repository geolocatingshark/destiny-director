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

import datetime as dt
import json
import logging
import ssl
import typing as t
from os import getenv as __getenv

import hikari as h
import regex as re
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool, Pool

load_dotenv()
T = t.TypeVar("T")


@t.overload
def _getenv(key: str, default: int) -> int: ...


@t.overload
def _getenv(key: str, default: str) -> str: ...


@t.overload
def _getenv(key: str) -> str: ...


def _getenv(key: str, default: int | str | None = None) -> int | str:
    value = __getenv(key)

    if value is None:
        if default is None:
            raise ValueError(f"Environment variable '{key}' not found.")
        elif isinstance(default, int):
            return int(default)
        else:
            return default
    else:
        if isinstance(default, int):
            try:
                return int(value)
            except ValueError:
                raise ValueError(
                    f"Environment variable '{key}' must be an integer."
                ) from None

        return value


def _getbool(key: str, default: bool) -> bool:
    """Parse a boolean env var case-insensitively (``true``/``1``/``yes``/``on``).

    Replaces the ad-hoc ``_getenv(...) == "true"`` checks, which were inconsistent:
    case-sensitive for ``MYSQL_SSL`` (so ``MYSQL_SSL=True`` silently disabled SSL)
    and case-insensitive for ``DISABLE_BAD_CHANNELS``.
    """
    value = __getenv(key)
    if value is None:
        return default
    return value.strip().lower() in {"true", "1", "yes", "on"}


def _test_env(var_name: str) -> tuple[int, ...] | tuple[()]:
    test_env = _getenv(var_name, default="false")
    test_env = test_env.lower()
    test_env = (
        tuple(int(env.strip()) for env in test_env.split(","))
        if test_env != "false"
        else ()
    )
    return test_env


def _db_urls(var_name: str, var_name_alternative: str) -> tuple[str, str]:
    try:
        db_url = _getenv(var_name)
    except ValueError:
        db_url = _getenv(var_name_alternative)

    if not db_url:
        # Added for compatiblity with Library Mode
        # db_url will only be none if the library mode environment
        # variable switch is enabled since _getenv(var_name_alternative)
        # would otherwise raise ValueError
        db_url = "://"

    __repl_till = db_url.find("://")
    db_url = db_url[__repl_till:]
    db_url_async = "mysql+asyncmy" + db_url
    db_url = "mysql" + db_url
    return db_url, db_url_async


def _db_config() -> tuple[
    t.Mapping[str, bool | type[AsyncSession]],
    t.Mapping[str, bool],
    t.Mapping[str, ssl.SSLContext],
    t.Mapping[str, int | str | bool | type[Pool]],
]:
    db_session_kwargs_sync: dict[str, t.Any] = {
        "expire_on_commit": False,
    }
    db_session_kwargs = db_session_kwargs_sync | {
        "class_": AsyncSession,
    }

    db_connect_args: t.Mapping[str, ssl.SSLContext] = {}
    if _getbool("MYSQL_SSL", True):
        ssl_ctx = ssl.create_default_context(
            cafile="/etc/ssl/certs/ca-certificates.crt"
        )
        ssl_ctx.verify_mode = ssl.CERT_REQUIRED
        db_connect_args.update({"ssl": ssl_ctx})

    db_engine_args: dict[str, int | str | bool | type[Pool]] = {
        "max_overflow": -1,
        "isolation_level": "READ COMMITTED",
        "pool_pre_ping": True,
        "pool_recycle": 3600,
        "pool_use_lifo": True,
    }
    # Under pytest the engine is driven from many short-lived event loops
    # (every asyncio.run() in test setup/teardown opens a new loop). asyncmy
    # connections are bound to their creating loop, so pooled connections that
    # outlive that loop blow up with "Event loop is closed" when the pool later
    # terminates them. NullPool closes each connection on return, within the
    # live loop, so nothing survives to a dead loop.
    if __getenv("PYTEST_VERSION") is not None:
        db_engine_args["poolclass"] = NullPool
        # max_overflow and pool_use_lifo are QueuePool-specific and rejected
        # by create_engine when combined with NullPool.
        del db_engine_args["max_overflow"]
        del db_engine_args["pool_use_lifo"]
    return db_session_kwargs, db_session_kwargs_sync, db_connect_args, db_engine_args


def _sheets_credentials(
    proj_id: str,
    priv_key_id: str,
    priv_key: str,
    client_email: str,
    client_id: str,
    client_x509_cert_url: str,
) -> dict[str, str]:
    priv_key = _getenv(priv_key)
    priv_key = priv_key.replace("\\n", "\n")
    gsheets_credentials = {
        "type": "service_account",
        "project_id": _getenv(proj_id),
        "private_key_id": _getenv(priv_key_id),
        "private_key": priv_key,
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
discord_token_anchor = _getenv("DISCORD_TOKEN_ANCHOR", default="")
discord_token_beacon = _getenv("DISCORD_TOKEN_BEACON", default="")
disable_bad_channels = _getbool("DISABLE_BAD_CHANNELS", False)

# Discord control server config
control_discord_server_id = int(_getenv("CONTROL_DISCORD_SERVER_ID", "-1"))
control_discord_role_id = _getenv("CONTROL_DISCORD_ROLE_ID", "-1")
kyber_discord_server_id = _getenv("KYBER_DISCORD_SERVER_ID", default=-1)
log_channel = _getenv("LOG_CHANNEL_ID", default=0)
alerts_channel = _getenv("ALERTS_CHANNEL_ID", default=0)


# Discord constants
embed_default_color = h.Color(int(_getenv("EMBED_DEFAULT_COLOR", "0"), 16))
embed_error_color = h.Color(int(_getenv("EMBED_ERROR_COLOR", "0"), 16))
followables: dict[str, int] = json.loads(_getenv("FOLLOWABLES", "{}"), parse_int=int)
default_url = _getenv("DEFAULT_URL", "")
# Seconds a paginator waits for interaction before timing out. Baked in — prod ran
# NAVIGATOR_TIMEOUT=900, never per-deploy overridden.
navigator_timeout = 900

# Discord logging / alerting config (see dd/common/discord_logging.py)
# Minimum log level forwarded to the alerts channel.
alert_min_level = _getenv("ALERT_MIN_LEVEL", "ERROR")
# Seconds the consumer waits collecting records before flushing a batch (lets
# duplicate records within the window collapse into a single alert).
# The knobs below have baked-in sensible defaults and are never overridden
# per-deploy, so they are plain constants (not env-backed) to keep the env contract
# small. Re-introduce env-backing only if a per-deploy override is ever needed.
alert_flush_interval = 5
# Max queued records before new ones are dropped (back-pressure guard).
alert_queue_maxsize = 1000
# Rolling window (seconds) and occurrence threshold for the "error storm"
# escalation, plus a debounce so a sustained storm doesn't re-ping every flush.
alert_freq_window = 300
alert_freq_threshold = 10
alert_escalation_debounce = 600
# Fraction of a mirror run's targets that must fail (and minimum sample size)
# before a "majority of mirrors failing" critical alert fires.
mirror_failure_ratio_threshold = 0.5
mirror_failure_min_sample = 10
# Mirror fan-out tuning. These are baked-in defaults (not env-backed) to keep the
# env contract small; re-introduce env-backing if a per-deploy override is ever
# needed. mirror_max_concurrency caps in-flight kernel coroutines; mirror_rate_per_sec
# is the global token-bucket rate shared across all runs (kept below Discord's ~50/s
# global REST budget to leave headroom for interactive commands). The retry window is
# the randomised delay (seconds) before a transient failure is retried.
mirror_max_concurrency = 8
mirror_rate_per_sec = 30.0
mirror_retry_min = 180
mirror_retry_max = 300
# Seconds an autopost announcer may stall (API offline / edit failing) before a
# single critical alert fires for that run.
announcer_offline_alert_after = 900
# Accent colours for alert severities (hex, like the other embed colours).
embed_warning_color = h.Color(0xF1C40F)
embed_critical_color = h.Color(0x992D22)

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

# Static Images / Resources
gunsmith_image_url = _getenv("GUNSMITH_IMAGE_URL")
lost_sector_gif_url = _getenv("LOST_SECTOR_GIF_URL")
xur_image_url = _getenv("XUR_IMAGE_URL")

# Bungie credentials
bungie_api_key = _getenv("BUNGIE_API_KEY", "")
bungie_client_id = _getenv("BUNGIE_CLIENT_ID", "")
bungie_client_secret = _getenv("BUNGIE_CLIENT_SECRET", "")


port = _getenv("PORT", 8080)
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
    r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"
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
