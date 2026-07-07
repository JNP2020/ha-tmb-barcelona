"""The TMB (Transports Metropolitans de Barcelona) integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TmbApiClient, TmbApiError
from .const import (
    CONF_APP_ID,
    CONF_APP_KEY,
    CONF_LINE_CODE,
    CONF_LINE_COLOR,
    CONF_MODE,
    CONF_SCAN_INTERVAL,
    CONF_STOPS,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    FRONTEND_URL_BASE,
    MODE_BUS,
)
from .coordinator import TmbCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# Deliberately not nested under hass.data[DOMAIN], which holds only
# per-config-entry data (see async_unload_entry's "last entry" check).
_FRONTEND_REGISTERED_KEY = f"{DOMAIN}_frontend_registered"

SERVICE_PLAN_TRIP = "plan_trip"
ATTR_FROM_LATITUDE = "from_latitude"
ATTR_FROM_LONGITUDE = "from_longitude"
ATTR_TO_LATITUDE = "to_latitude"
ATTR_TO_LONGITUDE = "to_longitude"

PLAN_TRIP_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_FROM_LATITUDE): cv.latitude,
        vol.Required(ATTR_FROM_LONGITUDE): cv.longitude,
        vol.Required(ATTR_TO_LATITUDE): cv.latitude,
        vol.Required(ATTR_TO_LONGITUDE): cv.longitude,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up TMB from a config entry."""
    await _async_register_frontend_resources(hass)

    client = TmbApiClient(
        async_get_clientsession(hass),
        entry.data[CONF_APP_ID],
        entry.data[CONF_APP_KEY],
    )

    items = entry.options.get(CONF_STOPS, [])
    items, colors_changed = await _async_backfill_line_colors(client, items)
    if colors_changed:
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_STOPS: items}
        )

    scan_interval = timedelta(
        seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS)
    )
    coordinator = TmbCoordinator(hass, client, items, scan_interval)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    async def _async_plan_trip(call: ServiceCall) -> ServiceResponse:
        """Look up point-to-point itineraries via TMB's trip planner."""
        try:
            itineraries = await client.async_get_itineraries(
                call.data[ATTR_FROM_LATITUDE],
                call.data[ATTR_FROM_LONGITUDE],
                call.data[ATTR_TO_LATITUDE],
                call.data[ATTR_TO_LONGITUDE],
            )
        except TmbApiError as err:
            raise HomeAssistantError(f"Error querying TMB planner: {err}") from err
        return {"itineraries": itineraries}

    if not hass.services.has_service(DOMAIN, SERVICE_PLAN_TRIP):
        hass.services.async_register(
            DOMAIN,
            SERVICE_PLAN_TRIP,
            _async_plan_trip,
            schema=PLAN_TRIP_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_backfill_line_colors(
    client: TmbApiClient, items: list[dict]
) -> tuple[list[dict], bool]:
    """Fill in `line_color` for items configured before it was tracked (pre-1.1).

    Their stored options predate the line_color field entirely, so without
    this they'd be stuck showing a generic grey pill in the timetable card
    forever instead of the line's real brand color, with no way to fix it
    short of removing and re-adding the stop.
    """
    if all(item.get(CONF_LINE_COLOR) for item in items):
        return items, False

    colors_by_mode: dict[str, dict[str, str | None]] = {}

    async def _color_for(mode: str, line_code: str) -> str | None:
        if mode not in colors_by_mode:
            lines = (
                await client.async_get_bus_lines()
                if mode == MODE_BUS
                else await client.async_get_metro_lines()
            )
            colors_by_mode[mode] = {line["code"]: line["color"] for line in lines}
        return colors_by_mode[mode].get(line_code)

    changed = False
    patched = []
    for item in items:
        if item.get(CONF_LINE_COLOR):
            patched.append(item)
            continue
        try:
            color = await _color_for(item[CONF_MODE], item[CONF_LINE_CODE])
        except TmbApiError:
            patched.append(item)
            continue
        patched.append({**item, CONF_LINE_COLOR: color})
        changed = True
    return patched, changed


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options (e.g. monitored stops) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_PLAN_TRIP)
    return unload_ok


async def _async_register_frontend_resources(hass: HomeAssistant) -> None:
    """Serve custom_components/tmb/www/ at FRONTEND_URL_BASE, once per run,
    and auto-add tmb-timetable-card.js as a Lovelace resource.

    Serving the file isn't enough on its own — the browser only loads it if
    a dashboard resource points at the URL, and the UI to add one
    (Settings -> Dashboards -> Resources) is hidden unless the user's
    profile has Advanced Mode on, an easy step to miss entirely. Auto-adding
    it (storage-mode dashboards only; YAML-mode has no collection to write
    to) means the card works without that manual step.
    """
    if hass.data.get(_FRONTEND_REGISTERED_KEY):
        return
    www_dir = str(Path(__file__).parent / "www")
    try:
        try:
            # Modern, non-deprecated API (HA 2024.7+).
            from homeassistant.components.http import StaticPathConfig

            await hass.http.async_register_static_paths(
                [StaticPathConfig(FRONTEND_URL_BASE, www_dir, False)]
            )
        except ImportError:
            hass.http.register_static_path(FRONTEND_URL_BASE, www_dir, False)
    except Exception:  # noqa: BLE001 - never let a card-serving hiccup break setup
        _LOGGER.warning(
            "Could not register the tmb-timetable-card frontend resource; "
            "the sensors will still work, but the Lovelace card won't be "
            "available until this succeeds on a future reload.",
            exc_info=True,
        )
    hass.data[_FRONTEND_REGISTERED_KEY] = True

    await _async_register_lovelace_resource(hass)


async def _async_register_lovelace_resource(hass: HomeAssistant) -> None:
    """Add tmb-timetable-card.js as a Lovelace resource if not already present.

    Only possible for storage-mode dashboards (the default for UI-managed
    dashboards), which expose a writable resource collection; YAML-mode
    dashboards manage resources via the user's own ui-lovelace.yaml, which
    this can't safely edit, so those users still add it manually (README).
    """
    resource_url = f"{FRONTEND_URL_BASE}/tmb-timetable-card.js"
    try:
        lovelace_data = hass.data.get("lovelace")
        if lovelace_data is None or lovelace_data.resource_mode != "storage":
            return
        resources = lovelace_data.resources
        await resources.async_get_info()  # ensures the collection is loaded
        if any(item.get("url") == resource_url for item in resources.async_items() or []):
            return
        await resources.async_create_item({"res_type": "module", "url": resource_url})
        _LOGGER.debug("Registered %s as a Lovelace resource", resource_url)
    except Exception:  # noqa: BLE001 - never let this block sensor setup
        _LOGGER.warning(
            "Could not auto-register the tmb-timetable-card dashboard "
            "resource; add %s manually instead (Settings -> Dashboards -> "
            "Resources — see the README).",
            resource_url,
            exc_info=True,
        )
