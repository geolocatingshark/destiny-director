"""Thin Bungie HTTP client: fetching only, returning raw JSON dicts.

This is the one place that opens aiohttp sessions, sets the ``X-API-Key`` +
``Authorization`` headers, and maps Bungie ``ErrorCode``s to exceptions. Parsing the
returned dicts into domain objects lives in :mod:`.models` (``from_*_response`` /
``parse_*``), so the models stay pure and unit-testable without faking HTTP.
"""

import typing as t

import aiohttp

from dd.common import schemas

from .constants import (
    API_GET_MEMBERSHIPS,
    API_MILESTONES,
    API_PROFILE,
    API_VENDORS_AUTHENTICATED,
    VENDOR_NOT_FOUND_ERROR_CODE,
    components,
)
from .models import MissingResponseField, VendorNotFound


def _headers(access_token: str) -> dict[str, str]:
    return {
        "X-API-Key": schemas.BungieCredentials.api_key,
        "Authorization": f"Bearer {access_token}",
    }


async def fetch_memberships(
    session: aiohttp.ClientSession, access_token: str
) -> dict[str, t.Any]:
    """GET the current user's memberships; returns the raw ``Response`` payload."""
    async with session.get(API_GET_MEMBERSHIPS, headers=_headers(access_token)) as resp:
        return (await resp.json())["Response"]


async def fetch_public_milestones(
    session: aiohttp.ClientSession,
) -> dict[str, t.Any]:
    """GET public milestones (unauthenticated — ``X-API-Key`` only).

    Returns the raw ``Response`` map keyed by milestone hash; each entry carries
    ``activities`` (with ``activityHash``) plus ``startDate``/``endDate``.
    """
    headers = {"X-API-Key": schemas.BungieCredentials.api_key}
    async with session.get(API_MILESTONES, headers=headers) as resp:
        return (await resp.json())["Response"]


async def fetch_profile(
    session: aiohttp.ClientSession,
    access_token: str,
    membership_type: int,
    membership_id: int,
) -> dict[str, t.Any]:
    """GET a Destiny profile (characters); returns the raw ``Response`` payload."""
    url = API_PROFILE.format(
        membership_type=membership_type, membership_id=membership_id
    )
    async with session.get(url, headers=_headers(access_token)) as resp:
        return (await resp.json())["Response"]


async def fetch_vendor(
    access_token: str,
    membership_type: int,
    membership_id: int,
    character_id: int,
    vendor_hash: int,
) -> dict[str, t.Any]:
    """GET a single authenticated vendor; returns the raw ``Response`` payload.

    Raises :class:`.models.VendorNotFound` when the vendor is not currently
    available, and :class:`.models.MissingResponseField` when the payload has no
    ``Response`` (e.g. an unexpected API error).
    """
    async with (
        aiohttp.ClientSession() as session,
        session.get(
            API_VENDORS_AUTHENTICATED.format(
                membershipType=membership_type,
                destinyMembershipId=membership_id,
                characterId=character_id,
                vendorHash=vendor_hash,
                components=components,
            ),
            headers=_headers(access_token),
        ) as resp,
    ):
        response = await resp.json()

    if response["ErrorCode"] == VENDOR_NOT_FOUND_ERROR_CODE:
        raise VendorNotFound("Vendor not found", api_response=response)

    if "Response" not in response:
        raise MissingResponseField(
            "Response",
            api_response=response,
            request_details=f"Vendor hash: {vendor_hash}",
        )

    return response["Response"]
