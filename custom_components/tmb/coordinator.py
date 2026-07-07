"""DataUpdateCoordinator for the TMB integration.

Both TMB real-time services (`ibus/stops/{stop}` and
`itransit/metro/estacions`) return *every* line serving a stop/station in a
single call, regardless of any line filter — so a stop code (bus) or
station code (metro) only needs to be fetched once per tick no matter how
many monitored lines are configured at it. This coordinator fetches each
distinct stop/station once, then filters the results per configured item
(mode + stop/station + line) down to a flat, nearest-first arrival list.

The two real-time services also disagree on which line identifier they
return: `ibus/stops/{stop}` reports a line's *display* code (e.g. "V21",
matching `CONF_LINE_NAME`, which is populated from the catalog's
`NOM_LINIA`), while `itransit/metro/estacions` reports the same internal
numeric code the catalog uses as `CONF_LINE_CODE` (`codi_linia`/
`CODI_LINIA`). Filtering arrivals by the wrong one of the two silently
produces zero matches — the field to filter on is mode-dependent.

Unlike FGC's static daily timetable, iBus/iMetro only ever report
currently-imminent arrivals, so it's normal and expected for a configured
line/stop to have zero arrivals at any given moment (e.g. off-peak hours,
or simply between buses). Entities are therefore created once per
configured item regardless of whether any arrival happens to exist at
startup, rather than being derived from destinations seen on the first
refresh.
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
    CONF_LINE_NAME,
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
    line_name: str


def item_key(item: MonitoredItem) -> str:
    """Stable identifier for a configured (mode, stop, line) monitored item."""
    return f"{item[CONF_MODE]}_{item[CONF_STOP_CODE]}_{item[CONF_LINE_CODE]}"


class TmbCoordinator(DataUpdateCoordinator[dict[str, list[Arrival]]]):
    """Coordinator that keeps, per monitored item, a nearest-first arrival list."""

    def __init__(
        self, hass: HomeAssistant, client: TmbApiClient, items: list[MonitoredItem]
    ) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self._client = client
        self.items = items

    async def _async_update_data(self) -> dict[str, list[Arrival]]:
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

        result: dict[str, list[Arrival]] = {}
        for item in self.items:
            mode = item[CONF_MODE]
            stop_code = item[CONF_STOP_CODE]
            # ibus reports display codes (NOM_LINIA); itransit reports the
            # catalog's internal numeric code (CODI_LINIA) — see module docstring.
            line_filter = item[CONF_LINE_NAME] if mode == MODE_BUS else item[CONF_LINE_CODE]
            key = item_key(item)

            all_arrivals = await _fetch(mode, stop_code)
            matching = [
                arrival
                for arrival in all_arrivals
                if arrival["line_code"].strip().upper() == line_filter.strip().upper()
            ]
            matching.sort(key=lambda arrival: arrival["eta_sec"])
            result[key] = matching

        return result
