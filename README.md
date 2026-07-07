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
- **One sensor per configured line/stop**, showing minutes to the next
  arrival, its destination, and the following arrivals as an attribute.
- **Add/remove stops** later via Configure → options, without re-adding the
  integration.
- **`tmb-timetable-card`** — a live departure-board Lovelace card, styled
  after TMB's own station displays, showing every configured line at a
  station sorted by soonest arrival. Add one card per station; switch
  which station it shows by changing the card's `station` field, so the
  same card type covers every stop you've configured. See
  [Timetable card](#timetable-card) below.
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
include `mode`, `line`, `line_color`, `destination`, `stop_name`,
`stop_code`, `next_arrival`, and `upcoming` (destination + minutes for the
following arrivals on that line/stop).

## Timetable card

`tmb-timetable-card` renders every configured line at one station as a
live departure board (line badge in the line's own color, destination,
minutes), sorted soonest-first across bus and metro alike.

The card is served directly by the integration — no separate HACS
"plugin" install needed. Add it as a dashboard resource once:

1. Settings → Dashboards → ⋮ → Resources → Add resource.
2. URL: `/tmb_static/tmb-timetable-card.js`, type: JavaScript module.
3. Add the card to a dashboard (via the card picker, or manually):

   ```yaml
   type: custom:tmb-timetable-card
   station: Passeig de Gràcia   # must match a sensor's stop_name attribute
   rows: 6                       # optional, default 6
   ```

`station` must exactly match the `stop_name` attribute of the sensors you
want shown (visible on the sensor's Attributes in Developer Tools → States).
Add multiple cards, one per station, to cover every stop you've configured.

## License

MIT — see [LICENSE](LICENSE).
