"""Tests for the pure metro-alert parsing/classification logic."""
from __future__ import annotations

from custom_components.tmb.alerts import (
    alerts_for_line,
    line_status,
    parse_alerts,
    summarize,
)

NOW = 1_800_000_000


def _alert(lines=("L1",), code="PP1", etype="TRAFFIC", status="WARNING", dates=None, **extra):
    return {
        "entities": [{"line_name": name, "line_code": 1} for name in lines],
        "categories": {"effect_type": etype, "effect_code": code, "effect_status": status},
        "disruption_dates": dates if dates is not None else [],
        **extra,
    }


def test_non_list_payload_is_empty():
    assert parse_alerts(None, NOW) == []
    assert parse_alerts("oops", NOW) == []
    assert parse_alerts({"error": 1}, NOW) == []


def test_wrapped_payload_is_unwrapped():
    assert len(parse_alerts({"alerts": [_alert()]}, NOW)) == 1


def test_malformed_entries_are_dropped_not_raised():
    payload = [None, 5, {}, {"entities": "x"}, {"entities": [None, {}]}, _alert()]
    assert len(parse_alerts(payload, NOW)) == 1


def test_undated_alert_kept_and_flagged():
    (alert,) = parse_alerts([_alert()], NOW)
    assert alert["undated"] is True


def test_past_and_future_windows_are_dropped():
    past = _alert(dates=[{"begin_date": NOW - 200, "end_date": NOW - 100}])
    future = _alert(dates=[{"begin_date": NOW + 100, "end_date": NOW + 200}])
    assert parse_alerts([past, future], NOW) == []


def test_active_window_kept_with_bounds():
    (alert,) = parse_alerts(
        [_alert(dates=[{"begin_date": NOW - 100, "end_date": NOW + 100}])], NOW
    )
    assert alert["undated"] is False
    assert (alert["begin"], alert["end"]) == (NOW - 100, NOW + 100)


def test_open_ended_window_is_active():
    assert len(parse_alerts([_alert(dates=[{"begin_date": NOW - 5, "end_date": None}])], NOW)) == 1


def test_millisecond_epochs_are_normalized():
    ms = [{"begin_date": (NOW - 100) * 1000, "end_date": (NOW + 100) * 1000}]
    (alert,) = parse_alerts([_alert(dates=ms)], NOW)
    assert alert["begin"] == NOW - 100


def test_inverted_or_garbage_windows_are_ignored():
    bad = [
        {"begin_date": NOW + 10, "end_date": NOW - 10},
        {"begin_date": "abc", "end_date": 5},
        {"begin_date": 0, "end_date": 0},
        "nope",
    ]
    (alert,) = parse_alerts([_alert(dates=bad)], NOW)
    assert alert["undated"] is True


def test_rank_classification():
    codes = {"PP1": 2, "NP3": 2, "PP5": 3, "NP9": 3, "PP2": 1, "PP99": 0}
    for code, rank in codes.items():
        (alert,) = parse_alerts([_alert(code=code)], NOW)
        assert alert["rank"] == rank, code


def test_no_data_status_is_unranked():
    (alert,) = parse_alerts([_alert(code="PP5", status="NO_DATA")], NOW)
    assert alert["rank"] == 0


def test_infrastructure_alert_has_no_traffic_rank():
    (alert,) = parse_alerts([_alert(etype="INFRASTRUCTURE", code="X")], NOW)
    assert alert["infrastructure"] is True
    assert alert["rank"] == 0


def test_severe_flag():
    (a,) = parse_alerts([_alert(status="SEVERE", code="PP2")], NOW)
    assert a["severe"] is True
    (b,) = parse_alerts([_alert(code="PP5", status="WARNING")], NOW)
    assert b["severe"] is True


def test_alternate_effect_shape():
    raw = {
        "entities": [{"line_name": "L3"}],
        "effect": {"type": "traffic", "code": "pp5", "status": "severe"},
    }
    (alert,) = parse_alerts([raw], NOW)
    assert (alert["effect_type"], alert["effect_code"], alert["rank"]) == ("TRAFFIC", "PP5", 3)


def test_funicular_and_network_wide_entities():
    raw = {"entities": [{"line_name": "", "line_code": 99}, {"line_name": "ALL"}]}
    (alert,) = parse_alerts([raw], NOW)
    assert alert["lines"] == ["FM", "ALL"]


def test_alerts_for_line_includes_network_wide_and_is_case_insensitive():
    alerts = parse_alerts(
        [_alert(lines=("L1",)), _alert(lines=("L2",)), _alert(lines=("ALL",))], NOW
    )
    assert len(alerts_for_line(alerts, "l1")) == 2
    assert len(alerts_for_line(alerts, "L5")) == 1


def test_line_status_levels():
    def status(code, **kw):
        return line_status(parse_alerts([_alert(code=code, **kw)], NOW))

    assert line_status([]) == "normal"
    assert status("PP5") == "stopped"
    assert status("PP1") == "partial"
    assert status("PP2") == "disrupted"
    assert status("PP99") == "notices"


def test_text_extraction_and_summary_is_json_safe():
    import json

    (alert,) = parse_alerts([_alert(description={"ca": "Obres", "en": "Works"})], NOW)
    assert alert["text"] == "Works"
    json.dumps(summarize(alert))
