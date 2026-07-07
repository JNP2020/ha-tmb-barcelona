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

Two independent, stackable ways to skip real API calls entirely rather
than just receiving an empty result:
- Manual quiet hours: a single wall-clock window (e.g. 00:00-05:00)
  applied to every configured item alike.
- GTFS auto-skip: per-line, using today's actual published schedule (see
  gtfs.py) to recognize a line has no service running at all right now.
Both fail open — any error resolving either just means "poll normally,"
never "assume no service."
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import TypedDict

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import Arrival, TmbApiClient, TmbApiError, TmbAuthError
from .const import (
    CONF_LINE_CODE,
    CONF_LINE_NAME,
    CONF_MODE,
    CONF_STOP_CODE,
    DOMAIN,
    MODE_BUS,
    SERVICE_STATUS_NO_SERVICE,
    SERVICE_STATUS_OK,
    SERVICE_STATUS_QUIET_HOURS,
)
from .gtfs import GtfsError, GtfsScheduleCache

_LOGGER = logging.getLogger(__name__)


class MonitoredItem(TypedDict):
    mode: str
    stop_code: str
    line_code: str
    line_name: str
    line_color: str | None


def item_key(item: MonitoredItem) -> str:
    """Stable identifier for a configured (mode, stop, line) monitored item."""
    return f"{item[CONF_MODE]}_{item[CONF_STOP_CODE]}_{item[CONF_LINE_CODE]}"


def _seconds_since_midnight(value) -> int:
    return value.hour * 3600 + value.minute * 60 + value.second


def _parse_hhmmss(value: str) -> int:
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds)


def _in_quiet_hours(now_sec: int, start: str, end: str) -> bool:
    """Whether `now_sec` falls in [start, end), wrapping past midnight if end <= start."""
    start_sec = _parse_hhmmss(start)
    end_sec = _parse_hhmmss(end)
    if start_sec == end_sec:
        return False
    if start_sec < end_sec:
        return start_sec <= now_sec < end_sec
    return now_sec >= start_sec or now_sec < end_sec


class TmbCoordinator(DataUpdateCoordinator[dict[str, list[Arrival]]]):
    """Coordinator that keeps, per monitored item, a nearest-first arrival list."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: TmbApiClient,
        items: list[MonitoredItem],
        update_interval: timedelta,
        quiet_hours: tuple[str, str] | None = None,
        gtfs_cache: GtfsScheduleCache | None = None,
    ) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=update_interval)
        self._client = client
        self.items = items
        self._quiet_hours = quiet_hours
        self._gtfs_cache = gtfs_cache
        # item_key -> one of the SERVICE_STATUS_* constants, populated every
        # tick; sensor.py surfaces this so a "no data" sensor can say *why*.
        self.skip_reasons: dict[str, str] = {}

    async def _async_update_data(self) -> dict[str, list[Arrival]]:
        now_sec = _seconds_since_midnight(dt_util.now())
        in_quiet_hours = self._quiet_hours is not None and _in_quiet_hours(
            now_sec, *self._quiet_hours
        )

        gtfs_windows = {}
        if self._gtfs_cache is not None and not in_quiet_hours:
            line_names = {item[CONF_LINE_NAME] for item in self.items}
            try:
                gtfs_windows = await self._gtfs_cache.async_get_windows(line_names)
            except GtfsError as err:
                _LOGGER.warning(
                    "Could not refresh GTFS schedule data, polling normally: %s", err
                )

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
            line_name = item[CONF_LINE_NAME]
            key = item_key(item)

            if in_quiet_hours:
                self.skip_reasons[key] = SERVICE_STATUS_QUIET_HOURS
                result[key] = []
                continue

            window = gtfs_windows.get(line_name)
            if window is not None and not window.covers(now_sec):
                self.skip_reasons[key] = SERVICE_STATUS_NO_SERVICE
                result[key] = []
                continue

            self.skip_reasons[key] = SERVICE_STATUS_OK
            # ibus reports display codes (NOM_LINIA); itransit reports the
            # catalog's internal numeric code (CODI_LINIA) — see module docstring.
            line_filter = line_name if mode == MODE_BUS else item[CONF_LINE_CODE]
            all_arrivals = await _fetch(mode, stop_code)
            matching = [
                arrival
                for arrival in all_arrivals
                if arrival["line_code"].strip().upper() == line_filter.strip().upper()
            ]
            matching.sort(key=lambda arrival: arrival["eta_sec"])
            result[key] = matching

        return result
