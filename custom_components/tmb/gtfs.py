"""GTFS static schedule lookups.

Used to work out whether a configured line has *any* scheduled service
running right now at all (e.g. it's 3am and the line doesn't run
overnight, or it's a holiday a bus line skips) — separate from, and
stacked with, the user's manual quiet hours. When a line has no scheduled
service, polling it is pointless: the answer is always "nothing coming."

TMB republishes the full GTFS feed daily. Its `stop_times.txt` alone is
~50 MB covering the whole bus+metro network (~1.2M rows) — downloading
and streaming it in pure-Python csv takes a couple of seconds even so,
so this is fetched and parsed once, in a background thread, refreshed
once per calendar day, never on every coordinator tick.

Windows are computed **per line, not per exact stop**. Measured against
a real feed: roughly half of stop_times.txt's rows leave `arrival_time`
blank — standard GTFS practice for "non-timepoint" stops meant to be
interpolated between real timepoints along a route's shape, common on
high-frequency lines. Filtering to one exact stop_id risks landing on a
stop with zero explicit times for a given trip. A route's origin/terminus
stops are reliably timepoints, so aggregating explicit times across every
stop on the route instead is far more robust. This does mean the computed
window can run a little wider than one specific stop's true first/last
time — a deliberate, safe bias: it can only cause a few extra polls near
the edges of service, never cause a real arrival to be silently skipped.
"""
from __future__ import annotations

import asyncio
import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import date

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import API_BASE_URL

GTFS_URL = f"{API_BASE_URL}/static/datasets/gtfs.zip"

_WEEKDAY_FIELDS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

# Widens each computed window a bit further, on top of the route-vs-stop
# safety margin already inherent in aggregating per line: covers minor
# day-to-day schedule drift and the last leg of a trip already under way
# when its "last" timepoint technically passes.
_WINDOW_BUFFER_SEC = 20 * 60


class GtfsError(Exception):
    """Could not fetch or parse the GTFS feed."""


_SECONDS_PER_DAY = 24 * 60 * 60


@dataclass
class ServiceWindow:
    """A line's first/last explicit scheduled time today.

    Seconds since today's local midnight; `last_sec` may exceed 86400 for
    trips that run past midnight (GTFS's own convention for that), so
    compare against a same-based clock rather than a wrapped 0-86399 one.
    """

    first_sec: int
    last_sec: int

    def covers(self, now_sec: int) -> bool:
        """Whether `now_sec` (plain 0-86399 wall-clock seconds since
        midnight) falls within this window.

        A window's `last_sec` can exceed 86400 (a metro line open past
        midnight, e.g. until "24:36:20"), but `now_sec` never does — it's
        read straight off a clock. Just after real midnight (e.g. 00:20,
        now_sec=1200) that under-counts against such a window, since 1200
        looks nowhere near 88580; it's actually the *same* moment as
        87600 on the GTFS clock this window was built from. So a plain
        `now_sec` is checked first, and failing that, `now_sec + 1 day` —
        covering the early-morning tail of a window that opened yesterday
        without ever having to know which calendar day the window itself
        started on.
        """
        lo = self.first_sec - _WINDOW_BUFFER_SEC
        hi = self.last_sec + _WINDOW_BUFFER_SEC
        return (lo <= now_sec <= hi) or (lo <= now_sec + _SECONDS_PER_DAY <= hi)


def _read_rows(zf: zipfile.ZipFile, name: str):
    with zf.open(name) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        yield from csv.DictReader(text)


def _parse_gtfs_time(value: str) -> int:
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds)


def _active_service_ids(zf: zipfile.ZipFile, today: date) -> set[str]:
    weekday_field = _WEEKDAY_FIELDS[today.weekday()]
    today_str = today.strftime("%Y%m%d")

    active: set[str] = set()
    for row in _read_rows(zf, "calendar.txt"):
        if row[weekday_field] == "1" and row["start_date"] <= today_str <= row["end_date"]:
            active.add(row["service_id"])

    for row in _read_rows(zf, "calendar_dates.txt"):
        if row["date"] != today_str:
            continue
        if row["exception_type"] == "1":
            active.add(row["service_id"])
        elif row["exception_type"] == "2":
            active.discard(row["service_id"])

    return active


def _compute_service_windows(
    zip_bytes: bytes, line_names: set[str], today: date
) -> dict[str, ServiceWindow | None]:
    """Blocking (CPU-bound CSV streaming) — always run via an executor."""
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    active_services = _active_service_ids(zf, today)

    route_id_to_name: dict[str, str] = {}
    for row in _read_rows(zf, "routes.txt"):
        if row["route_short_name"] in line_names:
            route_id_to_name[row["route_id"]] = row["route_short_name"]

    # Small file (a handful of headway-based trips); their stop_times.txt
    # rows are reference templates, not literal departure times, so their
    # actual first/last service is this window instead.
    freq_windows: dict[str, tuple[int, int]] = {}
    for row in _read_rows(zf, "frequencies.txt"):
        freq_windows[row["trip_id"]] = (
            _parse_gtfs_time(row["start_time"]),
            _parse_gtfs_time(row["end_time"]),
        )

    trip_id_to_line: dict[str, str] = {}
    for row in _read_rows(zf, "trips.txt"):
        line_name = route_id_to_name.get(row["route_id"])
        if line_name is not None and row["service_id"] in active_services:
            trip_id_to_line[row["trip_id"]] = line_name

    collected: dict[str, list[int]] = {name: [] for name in line_names}
    for row in _read_rows(zf, "stop_times.txt"):
        line_name = trip_id_to_line.get(row["trip_id"])
        if line_name is None:
            continue
        if row["arrival_time"]:
            collected[line_name].append(_parse_gtfs_time(row["arrival_time"]))
        freq = freq_windows.get(row["trip_id"])
        if freq is not None:
            collected[line_name].extend(freq)

    return {
        name: ServiceWindow(min(times), max(times)) if times else None
        for name, times in collected.items()
    }


class GtfsScheduleCache:
    """Fetches and parses TMB's GTFS feed at most once per calendar day."""

    def __init__(self, hass: HomeAssistant, session: aiohttp.ClientSession) -> None:
        self._hass = hass
        self._session = session
        self._lock = asyncio.Lock()
        self._loaded_date: date | None = None
        self._windows: dict[str, ServiceWindow | None] = {}

    async def async_get_windows(
        self, line_names: set[str]
    ) -> dict[str, ServiceWindow | None]:
        """Return each requested line's service window, refreshing if needed.

        A line missing from the result (rather than mapped to None) means
        it hasn't been resolved yet, e.g. because refreshing failed —
        callers should treat that the same as "unknown, don't skip."
        """
        today = dt_util.now().date()
        async with self._lock:
            already_known = self._windows.keys()
            if self._loaded_date != today or not line_names <= already_known:
                await self._async_refresh(line_names | already_known, today)
        return {name: self._windows[name] for name in line_names if name in self._windows}

    async def _async_refresh(self, line_names: set[str], today: date) -> None:
        try:
            async with self._session.get(GTFS_URL) as resp:
                resp.raise_for_status()
                zip_bytes = await resp.read()
        except aiohttp.ClientError as err:
            raise GtfsError(f"Could not download the GTFS feed: {err}") from err

        try:
            windows = await self._hass.async_add_executor_job(
                _compute_service_windows, zip_bytes, line_names, today
            )
        except (zipfile.BadZipFile, KeyError, ValueError) as err:
            raise GtfsError(f"Could not parse the GTFS feed: {err}") from err

        self._windows = windows
        self._loaded_date = today
