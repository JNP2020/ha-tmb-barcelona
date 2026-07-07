"""Constants for the TMB (Transports Metropolitans de Barcelona) integration."""

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
CONF_SCAN_INTERVAL = "scan_interval"
CONF_QUIET_HOURS_ENABLED = "quiet_hours_enabled"
CONF_QUIET_HOURS_START = "quiet_hours_start"
CONF_QUIET_HOURS_END = "quiet_hours_end"
CONF_AUTO_SKIP_NO_SERVICE = "auto_skip_no_service"

MODE_BUS = "bus"
MODE_METRO = "metro"

API_BASE_URL = "https://api.tmb.cat/v1"

# Stored in options as a plain int (seconds) since timedelta isn't
# JSON-serializable; converted to a timedelta where the coordinator needs one.
DEFAULT_SCAN_INTERVAL_SECONDS = 30
MIN_SCAN_INTERVAL_SECONDS = 15
MAX_SCAN_INTERVAL_SECONDS = 300

# Quiet hours are stored as "HH:MM:SS" strings (TimeSelector's native
# format); these are just the form defaults, not enforced bounds.
DEFAULT_QUIET_HOURS_START = "00:00:00"
DEFAULT_QUIET_HOURS_END = "05:00:00"

ATTR_MODE = "mode"
ATTR_LINE = "line"
ATTR_LINE_COLOR = "line_color"
ATTR_DESTINATION = "destination"
ATTR_STOP_NAME = "stop_name"
ATTR_STOP_CODE = "stop_code"
ATTR_NEXT_ARRIVAL = "next_arrival"
ATTR_UPCOMING = "upcoming"
ATTR_SERVICE_STATUS = "service_status"

SERVICE_STATUS_OK = "ok"
SERVICE_STATUS_QUIET_HOURS = "quiet_hours"
SERVICE_STATUS_NO_SERVICE = "no_service_scheduled"

MANUFACTURER = "Transports Metropolitans de Barcelona"

# custom_components/tmb/www/ is served here so tmb-timetable-card.js can be
# added to any dashboard as a Lovelace resource without a separate HACS
# "plugin" repo.
FRONTEND_URL_BASE = "/tmb_static"
