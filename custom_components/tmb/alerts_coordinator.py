"""Coordinator for TMB metro service alerts.

Deliberately separate from the arrivals coordinator: alerts change on a
timescale of minutes, so they poll slowly (``ALERTS_SCAN_INTERVAL_SECONDS``)
and a failure here must never take arrival sensors down with it.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .alerts import MetroAlert
from .api import TmbApiClient, TmbApiError, TmbAuthError
from .const import ALERTS_SCAN_INTERVAL_SECONDS, DOMAIN

_LOGGER = logging.getLogger(__name__)


class TmbAlertsCoordinator(DataUpdateCoordinator[list[MetroAlert]]):
    """Keeps the list of metro alerts currently in force."""

    def __init__(self, hass: HomeAssistant, client: TmbApiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_alerts",
            update_interval=timedelta(seconds=ALERTS_SCAN_INTERVAL_SECONDS),
        )
        self._client = client

    async def _async_update_data(self) -> list[MetroAlert]:
        try:
            return await self._client.async_get_metro_alerts()
        except TmbAuthError as err:
            raise ConfigEntryAuthFailed("Invalid TMB app_id/app_key") from err
        except TmbApiError as err:
            raise UpdateFailed(f"Error fetching TMB metro alerts: {err}") from err
