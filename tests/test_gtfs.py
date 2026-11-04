"""Tests for GTFS window computation using a small synthetic feed."""
from __future__ import annotations

import io
import zipfile
from datetime import date

from custom_components.tmb.gtfs import (
    EffectiveWindow,
    ServiceWindow,
    _compute_service_windows_two_days,
    _parse_gtfs_time,
)


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


FEED = {
    "routes.txt": "route_id,route_short_name\nR1,V21\nR2,H16\n",
    "calendar.txt": (
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
        "start_date,end_date\n"
        "WK,1,1,1,1,1,0,0,20260101,20261231\n"
        "SUN,0,0,0,0,0,0,1,20260101,20261231\n"
    ),
    "calendar_dates.txt": "service_id,date,exception_type\n",
    "trips.txt": "route_id,service_id,trip_id\nR1,WK,T1\nR1,WK,T2\nR2,SUN,T3\n",
    "frequencies.txt": "trip_id,start_time,end_time\n",
    "stop_times.txt": (
        "trip_id,arrival_time,stop_id\n"
        "T1,05:30:00,A\nT1,,B\nT2,23:50:00,A\nT2,24:36:20,Z\nT3,09:00:00,A\n"
    ),
}


def test_parse_gtfs_time_beyond_24h():
    assert _parse_gtfs_time("24:36:20") == 24 * 3600 + 36 * 60 + 20


def test_windows_per_line_with_past_midnight_trips():
    # 2026-11-04 is a Wednesday (WK), 2026-11-03 a Tuesday (WK).
    today, yest = _compute_service_windows_two_days(
        _zip(FEED), {"V21", "H16"}, date(2026, 11, 4), date(2026, 11, 3)
    )
    assert today["V21"] == ServiceWindow(5 * 3600 + 1800, 24 * 3600 + 36 * 60 + 20)
    assert today["H16"] is None  # SUN-only line does not run on a Wednesday
    assert yest["V21"] is not None


def test_sunday_only_line_runs_on_sunday():
    today, _ = _compute_service_windows_two_days(
        _zip(FEED), {"H16"}, date(2026, 11, 1), date(2026, 10, 31)
    )
    assert today["H16"] == ServiceWindow(9 * 3600, 9 * 3600)


def test_calendar_dates_exception_removes_service():
    feed = {**FEED, "calendar_dates.txt": "service_id,date,exception_type\nWK,20261104,2\n"}
    today, _ = _compute_service_windows_two_days(
        _zip(feed), {"V21"}, date(2026, 11, 4), date(2026, 11, 3)
    )
    assert today["V21"] is None


def test_effective_window_covers_after_midnight_via_yesterday():
    win = EffectiveWindow(
        today=ServiceWindow(5 * 3600, 23 * 3600),
        yesterday=ServiceWindow(5 * 3600, 24 * 3600 + 40 * 60),
    )
    assert win.covers(30 * 60)  # 00:30 -> yesterday's tail
    assert not win.covers(3 * 3600)
    assert win.covers(12 * 3600)


def test_effective_window_fails_open_without_data():
    assert EffectiveWindow(today=None, yesterday=None).covers(3 * 3600)
