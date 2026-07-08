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
stop on the route instead is far more robust.

Each calendar day's schedule is computed and cached **alongside the
previous day's**, not in place of it. GTFS attributes a trip that runs
past midnight (e.g. last departure "24:36:20") to the service day it
*started* on, not the calendar day the clock reads during that trip — so
right after real midnight, a naive "recompute for today, discard
yesterday" cache would wrongly conclude a still-running night bus/metro
has no service at all, for however long that last run has left. Keeping
yesterday's window alongside today's, and checking a small hours-past-
midnight moment against yesterday's window shifted by a day, closes that
gap without having to know in advance which calendar day a given window
belongs to.
"""
from __future__ import annotations

import asyncio
import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta

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

_SECONDS_PER_DAY = 24 * 60 * 60

# How close to a line's first/last scheduled time today polling resumes/
# stops — tight enough to actually save API calls overnight, generous
# enough to absorb ordinary schedule drift (a last train running a few
# minutes late, etc). On top of the route-vs-stop safety margin already
# inherent in aggregating per line rather than per exact stop.
_WINDOW_BUFFER_SEC = 5 * 60


class GtfsError(Exception):
    """Could not fetch or parse the GTFS feed."""


@dataclass
class ServiceWindow:
    """A line's first/last explicit scheduled time on one specific service day.

    Seconds since that day's local midnight; `last_sec` may exceed 86400
    for trips that run past midnight (GTFS's own convention for that).
    """

    first_sec: int
    last_sec: int

    def covers(self, same_day_sec: int) -> bool:
        """Whether a clock reading on *this window's own service day*
        (which, for the tail end of a window, can itself exceed 86400)
        falls within it.
        """
        lo = self.first_sec - _WINDOW_BUFFER_SEC
        hi = self.last_sec + _WINDOW_BUFFER_SEC
        return lo <= same_day_sec <= hi


@dataclass
class EffectiveWindow:
    """A line's service window as of *right now*, combining today's own
    schedule with yesterday's still-possibly-running-past-midnight one.

    `today`/`yesterday` being `None` means "no explicit-timed service
    found for that day" (a real negative, e.g. a line that doesn't run on
    a holiday) — distinct from this object not existing at all, which
    callers should treat as "never resolved, don't skip on it."
    """

    today: ServiceWindow | None
    yesterday: ServiceWindow | None

    def covers(self, now_sec: int) -> bool:
        if self.today is None and self.yesterday is None:
            # No schedule data at all for this line on either relevant
            # day -- fail open rather than treat silence as "no service."
            return True
        if self.today is not None and self.today.covers(now_sec):
            return True
        if self.yesterday is not None and self.yesterday.covers(now_sec + _SECONDS_PER_DAY):
            return True
        return False


def _read_rows(zf: zipfile.ZipFile, name: str):
    with zf.open(name) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        yield from csv.DictReader(text)


def _parse_gtfs_time(value: str) -> int:
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds)


def _active_service_ids(zf: zipfile.ZipFile, day: date) -> set[str]:
    weekday_field = _WEEKDAY_FIELDS[day.weekday()]
    day_str = day.strftime("%Y%m%d")

    active: set[str] = set()
    for row in _read_rows(zf, "calendar.txt"):
        if row[weekday_field] == "1" and row["start_date"] <= day_str <= row["end_date"]:
            active.add(row["service_id"])

    for row in _read_rows(zf, "calendar_dates.txt"):
        if row["date"] != day_str:
            continue
        if row["exception_type"] == "1":
            active.add(row["service_id"])
        elif row["exception_type"] == "2":
            active.discard(row["service_id"])

    return active


def _compute_service_windows_two_days(
    zip_bytes: bytes, line_names: set[str], today: date, yesterday: date
) -> tuple[dict[str, ServiceWindow | None], dict[str, ServiceWindow | None]]:
    """Blocking (CPU-bound CSV streaming) — always run via an executor.

    Streams stop_times.txt (the ~50 MB file) exactly once for both days
    together rather than twice, bucketing each matching row into
    whichever of today's/yesterday's collections its trip belongs to.
    """
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    today_services = _active_service_ids(zf, today)
    yesterday_services = _active_service_ids(zf, yesterday)

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

    # trip_id -> (line_name, active today?, active yesterday?)
    trip_info: dict[str, tuple[str, bool, bool]] = {}
    for row in _read_rows(zf, "trips.txt"):
        line_name = route_id_to_name.get(row["route_id"])
        if line_name is None:
            continue
        service_id = row["service_id"]
        is_today = service_id in today_services
        is_yesterday = service_id in yesterday_services
        if is_today or is_yesterday:
            trip_info[row["trip_id"]] = (line_name, is_today, is_yesterday)

    today_collected: dict[str, list[int]] = {name: [] for name in line_names}
    yesterday_collected: dict[str, list[int]] = {name: [] for name in line_names}

    for row in _read_rows(zf, "stop_times.txt"):
        info = trip_info.get(row["trip_id"])
        if info is None:
            continue
        line_name, is_today, is_yesterday = info

        times: list[int] = []
        if row["arrival_time"]:
            times.append(_parse_gtfs_time(row["arrival_time"]))
        freq = freq_windows.get(row["trip_id"])
        if freq is not None:
            times.extend(freq)
        if not times:
            continue

        if is_today:
            today_collected[line_name].extend(times)
        if is_yesterday:
            yesterday_collected[line_name].extend(times)

    def _to_windows(collected: dict[str, list[int]]) -> dict[str, ServiceWindow | None]:
        return {
            name: ServiceWindow(min(times), max(times)) if times else None
            for name, times in collected.items()
        }

    return _to_windows(today_collected), _to_windows(yesterday_collected)


class GtfsScheduleCache:
    """Fetches and parses TMB's GTFS feed at most once per calendar day.

    Keeps today's and yesterday's computed windows side by side (see
    module docstring for why yesterday's still matters after midnight),
    pruning anything older.
    """

    def __init__(self, hass: HomeAssistant, session: aiohttp.ClientSession) -> None:
        self._hass = hass
        self._session = session
        self._lock = asyncio.Lock()
        self._windows_by_date: dict[date, dict[str, ServiceWindow | None]] = {}
        self._known_lines: set[str] = set()

    async def async_get_windows(self, line_names: set[str]) -> dict[str, EffectiveWindow]:
        """Return each requested line's effective (today+yesterday) window.

        A line's `EffectiveWindow` always exists in the result for every
        requested name, even if refreshing has never succeeded — its
        `covers()` fails open (returns True) in that case, same as if it
        were simply missing.
        """
        today = dt_util.now().date()
        yesterday = today - timedelta(days=1)
        async with self._lock:
            self._known_lines |= line_names
            have_both_days = today in self._windows_by_date and yesterday in self._windows_by_date
            have_all_lines = have_both_days and self._known_lines <= self._windows_by_date[today].keys()
            if not have_all_lines:
                await self._async_refresh(self._known_lines, today, yesterday)
            self._windows_by_date = {
                day: windows for day, windows in self._windows_by_date.items() if day >= yesterday
            }

        today_windows = self._windows_by_date.get(today, {})
        yesterday_windows = self._windows_by_date.get(yesterday, {})
        return {
            name: EffectiveWindow(
                today=today_windows.get(name), yesterday=yesterday_windows.get(name)
            )
            for name in line_names
        }

    async def _async_refresh(self, line_names: set[str], today: date, yesterday: date) -> None:
        try:
            async with self._session.get(GTFS_URL) as resp:
                resp.raise_for_status()
                zip_bytes = await resp.read()
        except aiohttp.ClientError as err:
            raise GtfsError(f"Could not download the GTFS feed: {err}") from err

        try:
            today_windows, yesterday_windows = await self._hass.async_add_executor_job(
                _compute_service_windows_two_days, zip_bytes, line_names, today, yesterday
            )
        except (zipfile.BadZipFile, KeyError, ValueError) as err:
            raise GtfsError(f"Could not parse the GTFS feed: {err}") from err

        self._windows_by_date[today] = today_windows
        self._windows_by_date[yesterday] = yesterday_windows
