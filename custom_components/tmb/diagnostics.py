"""Diagnostics support for TMB."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_APP_ID, CONF_APP_KEY, DOMAIN
from .coordinator import TmbCoordinator

TO_REDACT = {CONF_APP_KEY, CONF_APP_ID}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: TmbCoordinator = data["coordinator"]
    alerts = data.get("alerts_coordinator")
    return {
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval_s": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "skip_reasons": dict(coordinator.skip_reasons),
        },
        "alerts": None
        if alerts is None
        else {
            "last_update_success": alerts.last_update_success,
            "count": len(alerts.data or []),
            "data": alerts.data,
        },
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "entry_options": dict(entry.options),
        "coordinator_data": coordinator.data,
    }
