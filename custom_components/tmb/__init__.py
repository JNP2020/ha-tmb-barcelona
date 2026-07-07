"""The TMB (Transports Metropolitans de Barcelona) integration."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TmbApiClient, TmbApiError
from .const import CONF_APP_ID, CONF_APP_KEY, CONF_STOPS, DOMAIN
from .coordinator import TmbCoordinator

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
