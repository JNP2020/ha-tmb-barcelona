# Changelog

All notable changes to this integration are documented here. Versions
match `custom_components/tmb/manifest.json`.

## 1.9.0

- **Metro service alerts.** New `alerts.py` parser + separate
  `TmbAlertsCoordinator` (5-minute poll) for `/v1/alerts/metro/channels/WEB`.
  Metro arrival sensors gain `line_status` and `alerts`; a diagnostic
  *TMB Metro alerts* sensor summarizes the network. Optional via
  Settings → *Show metro service alerts* (default on). The alert payload
  shape was taken from an open-source consumer of the same feed and has
  not been verified against a live key — parsing is defensive and drops
  malformed entries instead of failing.
- **Rate limiting.** HTTP 429 is now a distinct `TmbRateLimitError`;
  the arrivals coordinator honors `Retry-After` and backs off, then
  restores the configured interval on the first success.
- **Timeouts everywhere.** All API calls have a 15 s total timeout; the
  GTFS download has a 180 s timeout and a 150 MB size cap, and a failed
  GTFS refresh is not retried for 30 minutes instead of re-downloading on
  every tick.
- Timetable card is cache-busted with the integration version (an existing
  card resource is updated in place) and its custom elements are
  define-guarded against double registration.
- Diagnostics now also redact `app_id` and include coordinator state,
  skip reasons and alerts.
- **Tests and CI.** Added a pytest suite (alerts, API client, coordinator
  logic, GTFS windows, sensors), `pyproject.toml` (ruff + pytest),
  `requirements_test.txt` and a *Tests and lint* workflow.
