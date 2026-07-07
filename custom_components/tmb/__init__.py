"""The TMB (Transports Metropolitans de Barcelona) integration."""
from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TmbApiClient, TmbApiError
from .const import CONF_APP_ID, CONF_APP_KEY, CONF_STOPS, DOMAIN, FRONTEND_URL_BASE
from .coordinator import TmbCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

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
    coordinator = TmbCoordinator(hass, client, entry.options.get(CONF_STOPS, []))
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
    """Serve custom_components/tmb/www/ at FRONTEND_URL_BASE, once per run.

    This lets tmb-timetable-card.js be added to any dashboard as a Lovelace
    resource without needing a separate HACS "plugin" repo — the
    integration serves its own static file directly.
    """
    if hass.data.get(DOMAIN, {}).get("_frontend_registered"):
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
    hass.data.setdefault(DOMAIN, {})["_frontend_registered"] = True
