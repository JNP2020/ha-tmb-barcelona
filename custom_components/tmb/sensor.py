"""Sensor platform for TMB."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_DESTINATION,
    ATTR_LINE,
    ATTR_LINE_COLOR,
    ATTR_MODE,
    ATTR_NEXT_ARRIVAL,
    ATTR_SERVICE_STATUS,
    ATTR_STOP_CODE,
    ATTR_STOP_NAME,
    ATTR_UPCOMING,
    CONF_LINE_COLOR,
    CONF_LINE_NAME,
    CONF_MODE,
    CONF_STOP_CODE,
    CONF_STOP_NAME,
    CONF_STOPS,
    DOMAIN,
    MANUFACTURER,
    MODE_BUS,
    SERVICE_STATUS_OK,
)
from .coordinator import TmbCoordinator, item_key


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up one sensor per configured (mode, line, stop).

    Unlike a static timetable, iBus/iMetro only report currently-imminent
    arrivals, so a configured item can legitimately have zero arrivals at
    any given moment (off-peak, between buses, etc). Entities are therefore
    created unconditionally here rather than derived from what the
    coordinator's first refresh happened to see.
    """
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: TmbCoordinator = data["coordinator"]

    entities = [
        TmbArrivalSensor(coordinator, entry, item)
        for item in entry.options.get(CONF_STOPS, [])
    ]
    async_add_entities(entities)


class TmbArrivalSensor(CoordinatorEntity[TmbCoordinator], SensorEntity):
    """Minutes remaining until the next arrival for one configured line/stop."""

    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_attribution = "Data provided by Transports Metropolitans de Barcelona"

    def __init__(self, coordinator: TmbCoordinator, entry: ConfigEntry, item: dict) -> None:
        super().__init__(coordinator)
        self._item_key = item_key(item)
        self._mode = item[CONF_MODE]
        self._line_name = item[CONF_LINE_NAME]
        # .get(): entries added before line color was tracked (pre-1.1) won't have this key.
        self._line_color = item.get(CONF_LINE_COLOR)
        self._stop_code = item[CONF_STOP_CODE]
        self._stop_name = item[CONF_STOP_NAME]

        self._attr_icon = "mdi:bus-clock" if self._mode == MODE_BUS else "mdi:subway-variant"
        self._attr_name = f"TMB {self._line_name} - {self._stop_name}"
        self._attr_unique_id = f"{entry.entry_id}_{self._item_key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="TMB",
            manufacturer=MANUFACTURER,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _upcoming(self) -> list[dict]:
        return self.coordinator.data.get(self._item_key, [])

    @property
    def native_value(self) -> int | None:
        upcoming = self._upcoming
        if not upcoming:
            return None
        return max(0, upcoming[0]["eta_sec"] // 60)

    @property
    def extra_state_attributes(self) -> dict:
        attrs = {
            ATTR_MODE: self._mode,
            ATTR_LINE: self._line_name,
            ATTR_LINE_COLOR: self._line_color,
            ATTR_STOP_NAME: self._stop_name,
            ATTR_STOP_CODE: self._stop_code,
            ATTR_SERVICE_STATUS: self.coordinator.skip_reasons.get(
                self._item_key, SERVICE_STATUS_OK
            ),
        }
        upcoming = self._upcoming
        if not upcoming:
            return attrs
        attrs[ATTR_DESTINATION] = upcoming[0]["destination"]
        attrs[ATTR_NEXT_ARRIVAL] = max(0, upcoming[0]["eta_sec"] // 60)
        attrs[ATTR_UPCOMING] = [
            {
                ATTR_DESTINATION: arrival["destination"],
                ATTR_NEXT_ARRIVAL: max(0, arrival["eta_sec"] // 60),
            }
            for arrival in upcoming[1:]
        ]
        return attrs
