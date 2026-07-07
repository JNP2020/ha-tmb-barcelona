# TMB Barcelona for Home Assistant

A [HACS](https://hacs.xyz/) custom integration for **Transports Metropolitans
de Barcelona (TMB)** — real-time bus and metro arrival sensors for Home
Assistant, configured entirely through the UI.

This is a from-scratch, modernized replacement for the
[legacy `tmb` integration](https://www.home-assistant.io/integrations/tmb/)
shipped in Home Assistant core, which requires YAML, hand-typed stop/line
codes, and only supports buses. It's structured after
[`ha-fgc-trains`](https://github.com/JNP2020/ha-fgc-trains), adapted to TMB's
own API.

## Features

- **Config flow setup** — no YAML. Enter your TMB `app_id`/`app_key`, then
  pick a transport mode, line, and stop from searchable dropdowns (no need
  to know raw stop/line codes ahead of time).
- **Bus and metro** real-time arrivals, via TMB's `iBus` and `iTransit`
  real-time services.
- **One sensor per destination** at a stop — a stop served by a single
  line/direction gets one plain sensor; a stop where a line reverses
  direction gets one sensor per destination, each showing minutes to the
  next arrival plus the following arrivals as an attribute.
- **Add/remove stops** later via Configure → options, without re-adding the
  integration.
- **`tmb.plan_trip` service** — point-to-point itinerary lookup (walk +
  transit legs, duration, transfers) between two coordinates via TMB's
  trip planner, for use in automations/scripts.

## Not included (yet)

TMB's public API does not expose real-time vehicle GPS positions or
structured service-alert/incident feeds the way FGC's open-data portal
does, so there's no device tracker, live map, or alerts sensor here. If
TMB adds these to their public API, they'd be natural follow-ups.

## Requirements

Get your credentials from [developer.tmb.cat](https://developer.tmb.cat/):
sign in, create a new application, and note the generated `App ID` and
`App Key`.

## Installation

### HACS (recommended)

1. HACS → Integrations → ⋮ → Custom repositories → add this repository URL,
   category "Integration".
2. Install "TMB Barcelona", then restart Home Assistant.

### Manual

Copy `custom_components/tmb` into your Home Assistant `custom_components`
directory and restart.

## Setup

Settings → Devices & Services → Add Integration → "TMB Barcelona". Enter
your `App ID`/`App Key`, then pick the mode, line, and stop for your first
sensor. Add more stops afterwards via the integration's Configure menu.

## Entity attributes

Each arrival sensor's state is minutes until the next arrival. Attributes
include `mode`, `line`, `destination`, `stop_name`, `stop_code`,
`next_arrival`, and `upcoming` (minutes for the following arrivals at that
stop/line/destination).

## License

MIT — see [LICENSE](LICENSE).
