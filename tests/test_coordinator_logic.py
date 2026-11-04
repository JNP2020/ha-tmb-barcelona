"""Tests for coordinator helpers and the arrivals filtering/skip logic."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.tmb.api import TmbAuthError, TmbRateLimitError
from custom_components.tmb.coordinator import (
    TmbCoordinator,
    _in_quiet_hours,
    _parse_hhmmss,
    item_key,
)

H = 3600


def _item(mode="bus", stop="100", code="1", name="V21"):
    return {
        "mode": mode,
        "stop_code": stop,
        "line_code": code,
        "line_name": name,
        "line_color": None,
    }


def _arrival(line, eta, dest="X"):
    return {"line_code": line, "eta_sec": eta, "destination": dest}


def _coordinator(items, client, **kw):
    hass = MagicMock()
    coord = TmbCoordinator.__new__(TmbCoordinator)
    # Avoid DataUpdateCoordinator machinery; exercise _async_update_data directly.
    coord.hass = hass
    coord._client = client
    coord.items = items
    coord._quiet_hours = kw.get("quiet_hours")
    coord._gtfs_cache = kw.get("gtfs_cache")
    coord.skip_reasons = {}
    coord._base_interval = timedelta(seconds=30)
    coord.update_interval = timedelta(seconds=30)
    return coord


def test_parse_hhmmss():
    assert _parse_hhmmss("01:02:03") == 3723


@pytest.mark.parametrize(
    ("now", "start", "end", "expected"),
    [
        (2 * H, "00:00:00", "05:00:00", True),
        (5 * H, "00:00:00", "05:00:00", False),
        (23 * H, "22:00:00", "05:00:00", True),  # wraps midnight
        (3 * H, "22:00:00", "05:00:00", True),
        (12 * H, "22:00:00", "05:00:00", False),
        (12 * H, "08:00:00", "08:00:00", False),  # empty window
    ],
)
def test_quiet_hours(now, start, end, expected):
    assert _in_quiet_hours(now, start, end) is expected


def test_item_key_is_stable_and_distinguishes_modes():
    assert item_key(_item("bus")) != item_key(_item("metro"))
    assert item_key(_item()) == item_key(_item())


async def test_filters_by_display_code_for_bus_and_numeric_code_for_metro():
    client = SimpleNamespace(
        async_get_bus_arrivals=AsyncMock(
            return_value=[_arrival("v21", 300), _arrival("H16", 60), _arrival("V21", 120)]
        ),
        async_get_metro_arrivals=AsyncMock(return_value=[_arrival("1", 90), _arrival("2", 30)]),
    )
    bus, metro = _item("bus"), _item("metro", stop="200", code="1", name="L1")
    coord = _coordinator([bus, metro], client)
    data = await coord._async_update_data()
    assert [a["eta_sec"] for a in data[item_key(bus)]] == [120, 300]  # sorted, filtered
    assert [a["eta_sec"] for a in data[item_key(metro)]] == [90]


async def test_one_fetch_per_distinct_stop():
    client = SimpleNamespace(
        async_get_bus_arrivals=AsyncMock(return_value=[]),
        async_get_metro_arrivals=AsyncMock(return_value=[]),
    )
    coord = _coordinator([_item(code="1", name="A"), _item(code="2", name="B")], client)
    await coord._async_update_data()
    assert client.async_get_bus_arrivals.await_count == 1


async def test_quiet_hours_skip_without_api_calls(monkeypatch):
    monkeypatch.setattr(
        "custom_components.tmb.coordinator.dt_util.now",
        lambda: SimpleNamespace(hour=2, minute=0, second=0),
    )
    client = SimpleNamespace(async_get_bus_arrivals=AsyncMock())
    item = _item()
    coord = _coordinator([item], client, quiet_hours=("00:00:00", "05:00:00"))
    data = await coord._async_update_data()
    assert data[item_key(item)] == []
    assert coord.skip_reasons[item_key(item)] == "quiet_hours"
    client.async_get_bus_arrivals.assert_not_awaited()


async def test_auth_error_triggers_reauth():
    client = SimpleNamespace(async_get_bus_arrivals=AsyncMock(side_effect=TmbAuthError("x")))
    with pytest.raises(ConfigEntryAuthFailed):
        await _coordinator([_item()], client)._async_update_data()


async def test_rate_limit_backs_off_then_recovers():
    client = SimpleNamespace(
        async_get_bus_arrivals=AsyncMock(
            side_effect=[TmbRateLimitError("429", retry_after=600), []]
        )
    )
    coord = _coordinator([_item()], client)
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()
    assert coord.update_interval == timedelta(seconds=600)
    await coord._async_update_data()
    assert coord.update_interval == timedelta(seconds=30)


async def test_rate_limit_backoff_has_a_floor():
    client = SimpleNamespace(
        async_get_bus_arrivals=AsyncMock(side_effect=TmbRateLimitError("429"))
    )
    coord = _coordinator([_item()], client)
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()
    assert coord.update_interval >= timedelta(seconds=120)
