"""Pure parsing/classification of TMB's metro service-alert feed.

Endpoint: ``GET /v1/alerts/metro/channels/WEB`` (same ``app_id``/``app_key``
auth as the rest of the API). The payload is a JSON list of alerts; the
shape relied on here (cross-checked against the open-source
``taichikuji/trmnl-tmb-plugin`` consumer of the same feed) is::

    {
      "entities": [{"line_name": "L1", "line_code": 1}, ...],
      "categories": {"effect_type": "TRAFFIC", "effect_code": "PP1",
                     "effect_status": "SEVERE"},       # or "effect": {type, code, status}
      "disruption_dates": [{"begin_date": <epoch s|ms>, "end_date": <epoch s|ms>}],
      ...
    }

``line_name == "ALL"`` (or ``line_code == "ALL"``) means network-wide, and
``line_code == 99`` with no name is the Funicular de Montjuïc ("FM").

Nothing in this module touches the network or Home Assistant, so it is
unit-testable in isolation. Every field is read defensively: the feed is
external and undocumented, and a malformed alert must be dropped, never
crash the coordinator.
"""
from __future__ import annotations

from typing import Any, TypedDict

ALL_LINES = "ALL"
FUNICULAR_LINE = "FM"

# Severity ranks (higher = worse). Mirrors the classification used by the
# TRMNL reference plugin: effect codes that mean the line is (partly) stopped
# outrank ordinary traffic disruptions.
RANK_NONE = 0
RANK_DISRUPTION = 1
RANK_PARTIAL = 2
RANK_STOPPED = 3

_STOPPED_CODES = frozenset({"PP5", "PP10", "NP1", "NP2", "NP9"})
_PARTIAL_CODES = frozenset({"PP1", "NP3"})
_SEVERE_STATUSES = frozenset({"SEVERE", "CRITICAL"})

# Epoch values above this are milliseconds, not seconds.
_EPOCH_MS_THRESHOLD = 9_999_999_999


class MetroAlert(TypedDict):
    lines: list[str]  # normalized line names; may contain ALL_LINES
    effect_type: str
    effect_code: str
    status: str
    rank: int
    severe: bool
    infrastructure: bool
    undated: bool
    active: bool
    upcoming: bool
    begin: int | None  # epoch seconds
    end: int | None  # epoch seconds
    text: str | None


def _upper(value: Any) -> str:
    return str(value).strip().upper() if value is not None else ""


def _epoch_seconds(value: Any) -> int | None:
    """Normalize an epoch (s or ms) to seconds; None for missing/invalid/<=0."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    if number > _EPOCH_MS_THRESHOLD:
        number //= 1000
    return number


def _entity_line(entity: dict[str, Any]) -> str | None:
    name = str(entity.get("line_name") or "").strip()
    code = str(entity.get("line_code") if entity.get("line_code") is not None else "").strip()
    if name.upper() == ALL_LINES or code.upper() == ALL_LINES:
        return ALL_LINES
    if not name and code == "99":
        return FUNICULAR_LINE
    return name.upper() or None


def _first_text(alert: dict[str, Any]) -> str | None:
    """Best-effort human-readable text; the feed's text keys are not documented."""
    for key in ("description", "text", "header", "title", "message"):
        value = alert.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for lang in ("en", "ca", "es"):
                nested = value.get(lang)
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
    return None


def _windows(alert: dict[str, Any], now: int) -> tuple[bool, bool, bool, int | None, int | None]:
    """Return (dated, active, upcoming, begin, end) from ``disruption_dates``."""
    dated = active = upcoming = False
    begin_out: int | None = None
    end_out: int | None = None
    for window in alert.get("disruption_dates") or []:
        if not isinstance(window, dict):
            continue
        begin = _epoch_seconds(window.get("begin_date"))
        end = _epoch_seconds(window.get("end_date"))
        if window.get("begin_date") is not None and begin is None:
            continue
        if window.get("end_date") is not None and end is None:
            continue
        if begin is not None and end is not None and begin > end:
            continue
        if begin is None and end is None:
            continue
        dated = True
        if begin is not None and begin > now:
            upcoming = True
        elif end is None or end >= now:
            active = True
            begin_out, end_out = begin, end
    return dated, active, upcoming, begin_out, end_out


def parse_alerts(payload: Any, now: int) -> list[MetroAlert]:
    """Parse the raw feed into alerts that are relevant *right now*.

    Alerts whose dated windows are all in the past, or all in the future,
    are dropped (the TMB site only shows what is in force). Alerts with no
    usable dates are kept and flagged ``undated``.
    """
    if isinstance(payload, dict):
        for key in ("alerts", "data", "results"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        return []

    result: list[MetroAlert] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        categories = raw.get("categories") or raw.get("effect") or {}
        if not isinstance(categories, dict):
            categories = {}
        effect_type = _upper(categories.get("effect_type") or categories.get("type"))
        effect_code = _upper(categories.get("effect_code") or categories.get("code"))
        status = _upper(categories.get("effect_status") or categories.get("status"))

        lines: list[str] = []
        for entity in raw.get("entities") or []:
            if isinstance(entity, dict):
                line = _entity_line(entity)
                if line and line not in lines:
                    lines.append(line)
        if not lines:
            continue

        dated, active, upcoming, begin, end = _windows(raw, now)
        if dated and not active:
            continue

        rank = RANK_NONE
        if effect_type == "TRAFFIC" and status != "NO_DATA" and effect_code != "PP99":
            if effect_code in _STOPPED_CODES:
                rank = RANK_STOPPED
            elif effect_code in _PARTIAL_CODES:
                rank = RANK_PARTIAL
            else:
                rank = RANK_DISRUPTION
        severe = status in _SEVERE_STATUSES or rank == RANK_STOPPED

        result.append(
            MetroAlert(
                lines=lines,
                effect_type=effect_type,
                effect_code=effect_code,
                status=status,
                rank=rank,
                severe=severe,
                infrastructure=effect_type == "INFRASTRUCTURE",
                undated=not dated,
                active=True,
                upcoming=False,
                begin=begin,
                end=end,
                text=_first_text(raw),
            )
        )
    return result


def alerts_for_line(alerts: list[MetroAlert], line_name: str) -> list[MetroAlert]:
    """Alerts affecting ``line_name`` (network-wide alerts affect every line)."""
    wanted = line_name.strip().upper()
    return [a for a in alerts if ALL_LINES in a["lines"] or wanted in a["lines"]]


def line_status(line_alerts: list[MetroAlert]) -> str:
    """Collapse a line's alerts into one of the ``LINE_STATUS_*`` values."""
    if not line_alerts:
        return "normal"
    worst = max(alert["rank"] for alert in line_alerts)
    if worst >= RANK_STOPPED:
        return "stopped"
    if worst == RANK_PARTIAL:
        return "partial"
    if worst == RANK_DISRUPTION:
        return "disrupted"
    return "notices"


def summarize(alert: MetroAlert) -> dict[str, Any]:
    """Compact, JSON-serializable form for entity attributes."""
    return {
        "lines": alert["lines"],
        "effect_type": alert["effect_type"],
        "effect_code": alert["effect_code"],
        "status": alert["status"],
        "severe": alert["severe"],
        "undated": alert["undated"],
        "begin": alert["begin"],
        "end": alert["end"],
        "text": alert["text"],
    }
