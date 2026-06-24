import typing as t
from collections import defaultdict

import aiocron
import aiohttp
import aiohttp.web
import hikari as h
import lightbulb as lb
import regex as re

from dd.hmessage import HMessage

from ...common import cfg, schemas
from ...common.bot import CachedFetchBot
from ...common.utils import fetch_emoji_dict
from ..autopost import make_autopost_control_commands
from ..embeds import substitute_user_side_emoji
from . import (
    bungie_api as api,
    xur,
)

loader = lb.Loader()

# Exotic ornaments carry the exotic they reskin only in their description, as
# "...change the appearance of <Exotic Name>." This pulls that name out so the
# daily-offerings line can show "for <Exotic>".
_ORNAMENT_TARGET_RE = re.compile(r"change the appearance of (.+?)\.")
# traitIds that mark an item as an ornament (vs. another exotic cosmetic like a
# ghost shell or ship, which we deliberately leave without a "for ..." suffix).
_ORNAMENT_TRAIT_IDS = frozenset({"item.ornament.weapon", "item.ornament.armor"})


def _bright_dust_rotator_hashes(manifest_table: dict[str, t.Any]) -> list[int]:
    """Discover the daily bright-dust rotator vendor hashes from the manifest.

    These are the vendors whose ``vendorIdentifier`` starts with
    ``EVERVERSE_BRIGHT_DUST_ROTATOR`` (e.g. ``..._EXOTIC_GHOSTS``).
    """
    return [
        vendor_def["hash"]
        for vendor_def in manifest_table["DestinyVendorDefinition"].values()
        if vendor_def.get("vendorIdentifier", "").startswith(
            api.EVERVERSE_BRIGHT_DUST_ROTATOR_PREFIX
        )
    ]


def _exotic_ornament_target_name(
    item: api.DestinyItem, manifest_table: dict[str, t.Any]
) -> str | None:
    """Resolve the exotic an exotic ornament reskins, or ``None``.

    Only exotic ornaments (``traitIds`` containing ``item.ornament.weapon`` /
    ``item.ornament.armor``) carry a base item; their manifest description reads
    "...change the appearance of <Exotic>." Other exotic cosmetics (ghosts, ships,
    vehicles, emotes) and anything that doesn't match return ``None`` so no suffix
    is added.
    """
    manifest_entry = manifest_table["DestinyInventoryItemDefinition"].get(item.hash, {})

    trait_ids = manifest_entry.get("traitIds") or []
    if not _ORNAMENT_TRAIT_IDS.intersection(trait_ids):
        return None

    description = manifest_entry.get("displayProperties", {}).get("description", "")
    match = _ORNAMENT_TARGET_RE.search(description)
    return match.group(1) if match else None


def _group_daily_offerings(
    daily_items: list[api.DestinyItem],
) -> tuple[dict[str, list[api.DestinyItem]], list[api.DestinyItem]]:
    """Split daily offerings into per-class groups (class-specific armor ornaments,
    keyed by ``item.class_``) and a class-agnostic remainder — mirroring the weekly
    section's organisation so the post reads consistently."""
    by_class: dict[str, list[api.DestinyItem]] = {
        "Hunter": [],
        "Titan": [],
        "Warlock": [],
    }
    common: list[api.DestinyItem] = []
    for item in daily_items:
        if item.class_ in by_class:
            by_class[item.class_].append(item)
        else:
            common.append(item)
    return by_class, common


def _daily_offering_line(
    item: api.DestinyItem, manifest_table: dict[str, t.Any] | None
) -> str:
    """One rendered daily-offering line: ``name (cost) — type`` plus, for exotic
    ornaments, a ``for <exotic>`` suffix."""
    line = (
        f"• [{item.name}]({item.lightgg_url}) "
        f"({item.costs['Bright Dust']}) — {item.item_type_friendly_name}"
    )
    if item.is_exotic and manifest_table is not None:
        target = _exotic_ornament_target_name(item, manifest_table)
        if target:
            line += f" for {target}"
    return line


async def fetch_daily_bright_dust_offerings(
    webserver_runner: aiohttp.web.AppRunner,
) -> tuple[list[api.DestinyItem], dict[str, t.Any]]:
    """Fetch the deduped bright-dust items across all active rotator vendors.

    Returns the items (deduped by item hash) plus the manifest table, which the
    renderer needs for the exotic-ornament base-item lookup. Inactive rotators
    (``VendorNotFound``) are skipped so the post still succeeds.
    """
    access_token = await api.refresh_api_tokens(webserver_runner)

    async with aiohttp.ClientSession() as session:
        memberships = await api.client.fetch_memberships(session, access_token)
        membership = api.DestinyMembership.from_api_response(memberships)
        profile = await api.client.fetch_profile(
            session,
            access_token,
            membership.membership_type,
            membership.membership_id,
        )
        # Armor ornaments are class-specific: a vendor only returns the queried
        # character's class's ornaments, so the rotators must be queried once per
        # class to surface every class's offerings. Class-agnostic cosmetics (ghosts,
        # ships, shaders, weapon ornaments, …) come back identically for each and
        # dedupe by item hash below. Each item's ``class_`` is set from the manifest.
        character_ids = [
            membership.parse_character_id(profile, class_)
            for class_ in ("Hunter", "Titan", "Warlock")
        ]

    manifest_table = await api._build_manifest_dict(
        await api._get_latest_manifest(schemas.BungieCredentials.api_key)
    )
    rotator_hashes = _bright_dust_rotator_hashes(manifest_table)

    items: dict[int, api.DestinyItem] = {}  # dedupe by item hash across classes
    for character_id in character_ids:
        for vendor_hash in rotator_hashes:
            try:
                response = await api.client.fetch_vendor(
                    access_token=access_token,
                    membership_type=membership.membership_type,
                    membership_id=membership.membership_id,
                    character_id=character_id,
                    vendor_hash=vendor_hash,
                )
            except api.VendorNotFound:
                # Rotator is not currently active; skip it.
                continue

            vendor = api.DestinyVendor.from_vendors_api_response(
                response=response, manifest_table=manifest_table
            )
            for sale_item in vendor.sale_items:
                if "bright dust" in str(sale_item.costs).lower():
                    items.setdefault(sale_item.hash, sale_item)

    return list(items.values()), manifest_table


async def eververse_message_constructor(bot: CachedFetchBot) -> HMessage:
    classes = ["Hunter", "Titan", "Warlock"]
    sale_items_dict: dict[str, set[api.DestinyItem]] = defaultdict(set)
    eververse_data: api.DestinyVendor | None = None
    for class_ in classes:
        eververse_data = await xur.fetch_vendor_data(
            api.get_webserver_runner(),
            vendor_hashes=3361454721,
            character_class=class_,
        )
        for sale_item in eververse_data.sale_items:
            sale_items_dict[class_].add(sale_item)

    if eververse_data is None:
        raise RuntimeError("No eververse vendor data was fetched")

    # Eververse offers some items to only one class and some to every class.
    # Split the per-class sets into class-specific items (present in exactly one
    # class's set) and common items (present in all three) so each group can be
    # labelled with its class and rendered once.
    hunter_sale_items = sale_items_dict["Hunter"] - (
        sale_items_dict["Titan"] | sale_items_dict["Warlock"]
    )
    titan_sale_items = sale_items_dict["Titan"] - (
        sale_items_dict["Hunter"] | sale_items_dict["Warlock"]
    )
    warlock_sale_items = sale_items_dict["Warlock"] - (
        sale_items_dict["Hunter"] | sale_items_dict["Titan"]
    )
    common_sale_items = (
        sale_items_dict["Hunter"]
        & sale_items_dict["Titan"]
        & sale_items_dict["Warlock"]
    )

    for class_, class_specific_sale_items in zip(
        classes,
        [hunter_sale_items, titan_sale_items, warlock_sale_items],
        strict=True,
    ):
        for sale_item in class_specific_sale_items:
            sale_item.class_ = class_

    eververse_data.sale_items = list(
        hunter_sale_items | titan_sale_items | warlock_sale_items | common_sale_items
    )

    daily_items, daily_manifest_table = await fetch_daily_bright_dust_offerings(
        api.get_webserver_runner()
    )

    return await format_eververse_vendor(
        eververse_data,
        bot,
        daily_items=daily_items,
        manifest_table=daily_manifest_table,
    )


async def format_eververse_vendor(
    vendor: api.DestinyVendor,
    bot: CachedFetchBot,
    daily_items: list[api.DestinyItem] | None = None,
    manifest_table: dict[str, t.Any] | None = None,
) -> HMessage:
    # Sort items out into categories based on their item_type_friendly_name
    # then sort packages into Hunter, Titan and Warlock based on the source
    # data from the calling function

    emoji_dict = await fetch_emoji_dict(bot)

    hunter_specific_items: list[api.DestinyItem] = []
    titan_specific_items: list[api.DestinyItem] = []
    warlock_specific_items: list[api.DestinyItem] = []
    remaining_items: defaultdict[str, list[api.DestinyItem]] = defaultdict(list)

    for sale_item in vendor.sale_items:
        if "bright dust" not in str(sale_item.costs).lower():
            continue

        # Manually exclude DokiDoki Bundles from eververse returned from the API
        # The below two lines should be removed at a later date when this is not a
        # problem
        if sale_item.name.startswith("Doki Doki Destiny "):
            continue

        if sale_item.class_ == "Hunter":
            hunter_specific_items.append(sale_item)
        elif sale_item.class_ == "Titan":
            titan_specific_items.append(sale_item)
        elif sale_item.class_ == "Warlock":
            warlock_specific_items.append(sale_item)
        else:
            remaining_items[sale_item.item_type_friendly_name].append(sale_item)

    description = "# [This Week 𝘢𝘵 Eververse](https://kyber3000.com/Eververse)\n\n"
    description += "**__BRIGHT DUST OFFERINGS__** :bright_dust:\n\n"
    description += "⇣ All items below cost Bright Dust ⇣\n\n"

    for class_, class_specific_sale_items in zip(
        ["Hunter", "Titan", "Warlock"],
        [
            hunter_specific_items,
            titan_specific_items,
            warlock_specific_items,
        ],
        strict=True,
    ):
        if not class_specific_sale_items:
            continue

        description += f"**{class_} Specific Items**\n"
        for item in class_specific_sale_items:
            description += f"• {item.name} ({item.costs['Bright Dust']})\n"
        description += "\n"

    for item_type, items in remaining_items.items():
        description += f"**{item_type}s**\n"
        for item in items:
            description += (
                f"• [{item.name}]({item.lightgg_url}) ({item.costs['Bright Dust']})\n"
            )
        description += "\n"

    if daily_items:
        description += "**__DAILY BRIGHT DUST OFFERINGS__** :bright_dust:\n\n"
        daily_by_class, daily_common = _group_daily_offerings(daily_items)
        # Class-specific daily items (armor ornaments) grouped by class, like the
        # weekly section above; class-agnostic items follow.
        for class_ in ("Hunter", "Titan", "Warlock"):
            if not daily_by_class[class_]:
                continue
            description += f"**{class_} Specific Items**\n"
            for item in daily_by_class[class_]:
                description += _daily_offering_line(item, manifest_table) + "\n"
            description += "\n"
        for item in daily_common:
            description += _daily_offering_line(item, manifest_table) + "\n"
        description += "\n"

    description = await substitute_user_side_emoji(emoji_dict, description)

    embed = h.Embed(
        description=description,
        color=h.Color(cfg.embed_default_color),
        url="https://kyberscorner.com",
    )

    message = HMessage(embeds=[embed])
    return message


@loader.listener(h.StartedEvent)
async def on_start_schedule_autoposts(
    event: h.StartedEvent, bot: CachedFetchBot = lb.di.INJECTED
):
    # Run every Tuesday at 17:00 UTC
    @aiocron.crontab("0 17 * * TUE", start=True)
    # Use below crontab for testing to post every minute
    # @aiocron.crontab("* * * * *", start=True)
    async def autopost_eververse():
        await xur.api_to_discord_announcer(
            bot,
            channel_id=cfg.followables["eververse"],
            check_enabled=True,
            enabled_check_coro=schemas.AutoPostSettings.get_eververse_enabled,
            construct_message_coro=eververse_message_constructor,
        )


async def _get_eververse_enabled() -> bool:
    return bool(await schemas.AutoPostSettings.get_eververse_enabled())


_eververse_autopost_group = make_autopost_control_commands(
    autopost_name="eververse",
    enabled_getter=_get_eververse_enabled,
    enabled_setter=schemas.AutoPostSettings.set_eververse,
    channel_id=cfg.followables["eververse"],
    message_constructor_coro=eververse_message_constructor,
    message_announcer_coro=xur.api_to_discord_announcer,
)

loader.command(_eververse_autopost_group)
