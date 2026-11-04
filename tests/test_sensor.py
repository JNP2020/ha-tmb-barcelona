"""Entity-level tests: attributes and state without a running Home Assistant."""
from __future__ import annotations

from types import SimpleNamespace

from custom_components.tmb.alerts import parse_alerts
from custom_components.tmb.coordinator import item_key
from custom_components.tmb.sensor import TmbArrivalSensor, TmbMetroAlertsSensor

NOW = 1_800_000_000
ENTRY = SimpleNamespace(entry_id="abc")


def _metro_item():
    return {
        "mode": "metro",
        "stop_code": "111",
        "line_code": "1",
        "line_name": "L1",
        "line_color": "CE1126",
        "stop_name": "Catalunya",
    }


def _alerts(*raw):
    return SimpleNamespace(data=parse_alerts(list(raw), NOW))


def _raw(line, code="PP5"):
    return {
        "entities": [{"line_name": line}],
        "categories": {"effect_type": "TRAFFIC", "effect_code": code},
    }


def _arrival_sensor(item, arrivals, alerts):
    key = item_key(item)
    coord = SimpleNamespace(data={key: arrivals}, skip_reasons={})
    return TmbArrivalSensor(coord, ENTRY, item, alerts)


def test_metro_sensor_reports_line_status_and_alerts():
    sensor = _arrival_sensor(_metro_item(), [], _alerts(_raw("L1"), _raw("L2")))
    attrs = sensor.extra_state_attributes
    assert attrs["line_status"] == "stopped"
    assert len(attrs["alerts"]) == 1
    assert sensor.native_value is None


def test_unaffected_metro_line_is_normal():
    sensor = _arrival_sensor(_metro_item(), [], _alerts(_raw("L5")))
    assert sensor.extra_state_attributes["line_status"] == "normal"


def test_bus_sensor_has_no_alert_attributes():
    item = {**_metro_item(), "mode": "bus", "line_name": "V21"}
    sensor = _arrival_sensor(item, [], _alerts(_raw("V21")))
    assert "line_status" not in sensor.extra_state_attributes


def test_alerts_disabled_means_no_alert_attributes():
    sensor = _arrival_sensor(_metro_item(), [], None)
    assert "line_status" not in sensor.extra_state_attributes


def test_arrival_minutes_floor_and_upcoming():
    arrivals = [
        {"eta_sec": 190, "destination": "A"},
        {"eta_sec": 400, "destination": "B"},
    ]
    sensor = _arrival_sensor(_metro_item(), arrivals, None)
    assert sensor.native_value == 3
    assert sensor.extra_state_attributes["upcoming"][0]["destination"] == "B"


def test_alerts_sensor_counts_and_lists_affected_lines():
    sensor = TmbMetroAlertsSensor(_alerts(_raw("L1"), _raw("L3", "PP1")), ENTRY)
    assert sensor.native_value == 2
    assert sensor.extra_state_attributes["affected_lines"] == ["L1", "L3"]
    assert sensor.unique_id == "abc_metro_alerts"
