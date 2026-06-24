"""Bungie OAuth: state-code management, token refresh, and the callback webserver."""

import asyncio
import datetime as dt
import logging
from uuid import uuid4

import aiohttp
import aiohttp.web
from yarl import URL

from dd.common import cfg, schemas

from .constants import API_OAUTH_GET_TOKEN, API_ROOT, BUNGIE_NET

logger = logging.getLogger(__name__)

# Hard cap on how long /bungie login waits for the OAuth callback before giving up.
# The OAuth state code itself expires after 5 minutes (see generate_oauth_state_code),
# so a real login completes well within this; this bound just prevents a permanently
# hung command and a leaked callback webserver.
LOGIN_WAIT_TIMEOUT_SECONDS = 15 * 60


class OAuthStateManager:
    _oauth_state_codes: dict[str, dt.datetime] = {}
    _access_token: str | None = None
    _access_token_expires: dt.datetime | None = None

    @classmethod
    def _sweep_expired_state_codes(cls):
        """Drop expired login codes proactively.

        A code is otherwise only removed on consume or when something happens to
        check it, so an abandoned ``/bungie login`` (code generated, login never
        completed) leaks the entry. Sweeping here keeps the dict bounded.
        """
        now = dt.datetime.now()
        for code in [c for c, exp in cls._oauth_state_codes.items() if exp <= now]:
            cls._oauth_state_codes.pop(code, None)

    @classmethod
    def generate_oauth_state_code(cls):
        cls._sweep_expired_state_codes()
        while True:
            state_code = str(uuid4())
            if cls.check_state_code_exists(state_code):
                continue
            else:
                expiry = dt.datetime.now() + dt.timedelta(minutes=5)
                cls._oauth_state_codes[state_code] = expiry
                break

        return state_code

    @classmethod
    def consume_oauth_state_code(cls, state_code: str):
        expiry_date = cls._oauth_state_codes.pop(state_code)
        if expiry_date <= dt.datetime.now():
            raise ValueError("State code has expired or is incorrect.")

    @classmethod
    def check_state_code_exists(cls, state_code: str):
        try:
            if cls._oauth_state_codes[state_code] > dt.datetime.now():
                return True
            else:
                cls._oauth_state_codes.pop(state_code)
                return False
        except KeyError:
            return False

    # Note, chaining @classmethod and @property is deprecated in
    # python 3.13, hence the getter method here
    @classmethod
    def get_access_token(cls) -> str | None:
        if cls._access_token_expires and cls._access_token_expires > dt.datetime.now():
            return cls._access_token

    @classmethod
    def set_access_token(cls, access_token, access_token_expires: int):
        """NOTE: This is not stored in the db, and is instead a class variable"""
        cls._access_token = access_token
        cls._access_token_expires = dt.datetime.now() + dt.timedelta(
            seconds=access_token_expires * 0.8  # 20% Factor of Safety
        )

    @classmethod
    def clear_access_token(cls):
        cls._access_token = None
        cls._access_token_expires = None


# Get a url to send the user to for OAuth
def oauth_url():
    state_code = OAuthStateManager.generate_oauth_state_code()
    return (URL(BUNGIE_NET) / "en/OAuth/Authorize").with_query(
        client_id=schemas.BungieCredentials.client_id,
        response_type="code",
        state=state_code,
    )


class APIOfflineException(Exception):
    pass


async def check_bungie_api_online(raise_exception: bool = False) -> bool:
    async with (
        aiohttp.ClientSession() as session,
        session.get(
            f"{API_ROOT}/App/FirstParty",
            headers={"X-API-Key": schemas.BungieCredentials.api_key},
        ) as response,
    ):
        response = await response.json()
        if response["ErrorCode"] in [0, 1]:
            return True
        elif raise_exception:
            raise APIOfflineException(response)
        else:
            return False


def webserver_runner_preparation() -> aiohttp.web.AppRunner:
    app = aiohttp.web.Application()
    routes = aiohttp.web.RouteTableDef()

    @routes.get("/oauth/callback")
    async def handle_oauth_callback(request):
        # Extract the code from the callback URL
        try:
            code = request.query.get("code", "")
            state_code = request.query.get("state", "")

            OAuthStateManager.consume_oauth_state_code(state_code)

        except KeyError:
            return aiohttp.web.Response(text="Invalid callback URL")

        except ValueError:
            return aiohttp.web.Response(text="URL has expired or is incorrect")

        # Exchange the code for an access token

        async with (
            aiohttp.ClientSession() as session,
            session.post(
                API_OAUTH_GET_TOKEN,
                data={
                    "client_id": schemas.BungieCredentials.client_id,
                    "client_secret": schemas.BungieCredentials.client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                },
            ) as response,
        ):
            response_json = await response.json()

        try:
            OAuthStateManager.set_access_token(
                response_json["access_token"], response_json["expires_in"]
            )
            await schemas.BungieCredentials.set_refresh_token(
                refresh_token=response_json["refresh_token"],
                refresh_token_expires=response_json["refresh_expires_in"],
            )
        except KeyError:
            logger.error("Error during bungie api authentication: %s", response_json)
            return aiohttp.web.Response(text="Error during bungie api authentication")

        return aiohttp.web.Response(text="You can close this tab/window now.")

    app.add_routes(routes)
    runner = aiohttp.web.AppRunner(app)
    return runner


async def _wait_for_token_from_login(
    runner: aiohttp.web.AppRunner,
) -> str:
    logger.info("Waiting for access token...")

    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "0.0.0.0", cfg.port)
    await site.start()

    try:
        async with asyncio.timeout(LOGIN_WAIT_TIMEOUT_SECONDS):
            while not (_access_token := OAuthStateManager.get_access_token()):
                await asyncio.sleep(1)
        return _access_token
    finally:
        await runner.shutdown()
        await runner.cleanup()


async def refresh_api_tokens(
    runner: aiohttp.web.AppRunner, with_login: bool = False
) -> str:
    if with_login:
        OAuthStateManager.clear_access_token()
        _access_token = await _wait_for_token_from_login(runner)
        return _access_token

    bungie_credentials = await schemas.BungieCredentials.get_credentials()
    if not bungie_credentials:
        raise ValueError("Bungie credentials are not set, please log in")
    elif dt.datetime.now() > bungie_credentials.refresh_token_expires:
        raise ValueError("Bungie credentials have expired, please log in again")

    async with (
        aiohttp.ClientSession() as session,
        session.post(
            API_OAUTH_GET_TOKEN,
            data={
                "client_id": schemas.BungieCredentials.client_id,
                "client_secret": schemas.BungieCredentials.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": bungie_credentials.refresh_token,
            },
        ) as response,
    ):
        response_json = await response.json()
        _access_token = response_json["access_token"]
        _refresh_token = response_json["refresh_token"]
        _refresh_token_expires = response_json["refresh_expires_in"]

    await schemas.BungieCredentials.set_refresh_token(
        refresh_token=_refresh_token,
        refresh_token_expires=_refresh_token_expires,
    )

    return _access_token


# In lightbulb v2 the OAuth callback webserver runner lived on
# ``bot.d.webserver_runner``
# (set by the old ``register`` hook). With v3 there is no shared ``bot.d`` namespace, so
# the runner is kept as a module-level singleton built lazily on first use.
_webserver_runner: aiohttp.web.AppRunner | None = None


def get_webserver_runner() -> aiohttp.web.AppRunner:
    global _webserver_runner
    if _webserver_runner is None:
        _webserver_runner = webserver_runner_preparation()
    return _webserver_runner
