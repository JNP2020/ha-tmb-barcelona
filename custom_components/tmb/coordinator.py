"""DataUpdateCoordinator for the TMB integration.

Both TMB real-time services (`ibus/stops/{stop}` and
`itransit/metro/estacions`) return *every* line serving a stop/station in a
single call, regardless of any line filter — so a stop code (bus) or
station code (metro) only needs to be fetched once per tick no matter how
many monitored lines/destinations are configured at it. This coordinator
fetches each distinct stop/station once, then buckets the results per
configured item (mode + stop/station + line) by destination.
"""
from __future__ import annotations

import logging
from typing import TypedDict

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import Arrival, TmbApiClient, TmbApiError, TmbAuthError
from .const import (
    CONF_LINE_CODE,
    CONF_MODE,
    CONF_STOP_CODE,
    DOMAIN,
    MODE_BUS,
    SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class MonitoredItem(TypedDict):
    mode: str
    stop_code: str
    line_code: str


def item_key(item: MonitoredItem) -> str:
    """Stable identifier for a configured (mode, stop, line) monitored item."""
    return f"{item[CONF_MODE]}_{item[CONF_STOP_CODE]}_{item[CONF_LINE_CODE]}"


class TmbCoordinator(DataUpdateCoordinator[dict[str, dict[str, list[Arrival]]]]):
    """Coordinator that keeps, per monitored item, a destination -> arrivals map."""

    def __init__(
        self, hass: HomeAssistant, client: TmbApiClient, items: list[MonitoredItem]
    ) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self._client = client
        self.items = items
        # item_key -> sorted list of distinct destinations, populated after
        # the first refresh; sensor.py uses this once at setup to decide how
        # many entities to create per item.
        self.destinations: dict[str, list[str]] = {}

    async def _async_update_data(self) -> dict[str, dict[str, list[Arrival]]]:
        stop_arrivals_cache: dict[tuple[str, str], list[Arrival]] = {}

        async def _fetch(mode: str, code: str) -> list[Arrival]:
            cache_key = (mode, code)
            if cache_key in stop_arrivals_cache:
                return stop_arrivals_cache[cache_key]
            try:
                if mode == MODE_BUS:
                    arrivals = await self._client.async_get_bus_arrivals(code)
                else:
                    arrivals = await self._client.async_get_metro_arrivals(code)
            except TmbAuthError as err:
                raise ConfigEntryAuthFailed("Invalid TMB app_id/app_key") from err
            except TmbApiError as err:
                raise UpdateFailed(f"Error fetching TMB arrivals for {code}: {err}") from err
            stop_arrivals_cache[cache_key] = arrivals
            return arrivals

        result: dict[str, dict[str, list[Arrival]]] = {}
        for item in self.items:
            mode = item[CONF_MODE]
            stop_code = item[CONF_STOP_CODE]
            line_code = item[CONF_LINE_CODE]
            key = item_key(item)

            all_arrivals = await _fetch(mode, stop_code)
            by_destination: dict[str, list[Arrival]] = {}
            for arrival in all_arrivals:
                if arrival["line_code"].strip().upper() != line_code.strip().upper():
                    continue
                by_destination.setdefault(arrival["destination"], []).append(arrival)

            for destination, arrivals in by_destination.items():
                arrivals.sort(key=lambda arrival: arrival["eta_sec"])

            result[key] = by_destination
            self.destinations[key] = sorted(by_destination.keys())

        return result
