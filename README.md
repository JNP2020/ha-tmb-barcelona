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
  pick a transport mode, line, and stop from searchable dropdowns — sorted
  nearest-to-home first, with distances shown, so finding "the stop near
  me" on a line with 50+ stops doesn't mean scrolling an alphabetical list.
- **Bus and metro** real-time arrivals, via TMB's `iBus` and `iTransit`
  real-time services.
- **One sensor per configured line/stop**, showing minutes to the next
  arrival, its destination, and the following arrivals as an attribute.
- **Add/remove stops** later via Configure → options, without re-adding the
  integration.
- **Configurable poll interval** — Configure → Settings, 15–300 seconds
  (default 30), if you want to trade responsiveness for fewer API calls.
- **Diagnostics** — Settings → Devices & Services → TMB → ⋮ → Download
  diagnostics, for bug reports (the `app_key` is redacted).
- **`tmb-timetable-card`** — a live departure-board Lovelace card, styled
  after TMB's own station displays, showing every configured line sorted
  by soonest arrival, each in its own line's official color. Combine
  several stations into a single board (e.g. Urquinaona's L1 and L4
  platforms), or make one card per stop — see
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
following arrivals on that line/stop). Stops added before `line_color`
existed (pre-1.1) get it backfilled automatically the next time the
integration loads — no need to remove and re-add them.

## Timetable card

`tmb-timetable-card` renders every configured line at one or more stations
as a live departure board (line badge in the line's own official color,
destination, minutes), sorted soonest-first across bus and metro alike.

The card is served directly by the integration — no separate HACS
"plugin" install needed, and as of 1.5 it's added as a dashboard resource
**automatically** (storage-mode dashboards, the default). If your
dashboard shows "custom element doesn't exist: tmb-timetable-card" or a
blank card, that auto-registration didn't run yet — reload the integration
(Settings → Devices & Services → TMB → ⋮ → Reload) and hard-refresh the
dashboard. If you're on a YAML-mode dashboard, or it still doesn't show up,
add the resource by hand instead:

1. Settings → Dashboards → ⋮ → Resources → Add resource. (This menu is
   hidden unless Advanced Mode is on — Settings → your profile → scroll
   down → Advanced Mode.)
2. URL: `/tmb_static/tmb-timetable-card.js`, type: JavaScript module.
3. Add the card to a dashboard (via the card picker, or manually):

   ```yaml
   type: custom:tmb-timetable-card
   stations:
     - Passeig de Gràcia   # must match a sensor's stop_name attribute
   rows: 8                  # optional, default 8
   ```

   A single station also works with the shorter singular form:

   ```yaml
   type: custom:tmb-timetable-card
   station: Diagonal
   ```

`stations`/`station` must exactly match the `stop_name` attribute of the
sensors you want shown (visible on a sensor's Attributes in Developer
Tools → States).

### Combining stations on one board

List more than one station to merge them into a single board — each row
still shows its own line's color, so mixing lines/stations is safe:

```yaml
type: custom:tmb-timetable-card
stations:
  - Urquinaona   # matches both the L1 and L4 sensors at this station
  - Diagonal
```

Some physical stations (e.g. Urquinaona, served by both L1 and L4) expose
the same `stop_name` across their different lines' sensors — in that case
a single name already pulls in every line, and the header shows it once
rather than repeating it. If you list stations with genuinely different
names, the header joins them (e.g. "Urquinaona, Diagonal") unless you set
an explicit `title`.

## License

MIT — see [LICENSE](LICENSE).
