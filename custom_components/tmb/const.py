"""Constants for the TMB (Transports Metropolitans de Barcelona) integration."""
from datetime import timedelta

DOMAIN = "tmb"

CONF_APP_ID = "app_id"
CONF_APP_KEY = "app_key"
CONF_STOPS = "stops"

CONF_MODE = "mode"
CONF_LINE_CODE = "line_code"
CONF_LINE_NAME = "line_name"
CONF_LINE_COLOR = "line_color"
CONF_STOP_CODE = "stop_code"
CONF_STOP_NAME = "stop_name"

MODE_BUS = "bus"
MODE_METRO = "metro"

API_BASE_URL = "https://api.tmb.cat/v1"

SCAN_INTERVAL = timedelta(seconds=30)

ATTR_MODE = "mode"
ATTR_LINE = "line"
ATTR_LINE_COLOR = "line_color"
ATTR_DESTINATION = "destination"
ATTR_STOP_NAME = "stop_name"
ATTR_STOP_CODE = "stop_code"
ATTR_NEXT_ARRIVAL = "next_arrival"
ATTR_UPCOMING = "upcoming"

MANUFACTURER = "Transports Metropolitans de Barcelona"

# custom_components/tmb/www/ is served here so tmb-timetable-card.js can be
# added to any dashboard as a Lovelace resource without a separate HACS
# "plugin" repo.
FRONTEND_URL_BASE = "/tmb_static"
