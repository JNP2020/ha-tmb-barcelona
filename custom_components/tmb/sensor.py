"""Sensor platform for TMB."""
from __future__ import annotations

import re

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
    ATTR_MODE,
    ATTR_NEXT_ARRIVAL,
    ATTR_STOP_CODE,
    ATTR_STOP_NAME,
    ATTR_UPCOMING,
    CONF_LINE_CODE,
    CONF_LINE_NAME,
    CONF_MODE,
    CONF_STOP_CODE,
    CONF_STOP_NAME,
    CONF_STOPS,
    DOMAIN,
    MANUFACTURER,
    MODE_BUS,
)
from .coordinator import TmbCoordinator, item_key


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up one sensor per destination for each configured (mode, line, stop).

    The coordinator has already done its first refresh by the time this
    runs, so `coordinator.destinations` is populated for every configured
    item. A stop served by a single destination all day just gets one
    plainly-named sensor; a stop where the line reverses direction (or a
    metro line splits) gets one sensor per destination.
    """
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: TmbCoordinator = data["coordinator"]

    entities = []
    for item in entry.options.get(CONF_STOPS, []):
        key = item_key(item)
        destinations = coordinator.destinations.get(key, [])
        single_destination = len(destinations) <= 1
        for destination in destinations:
            entities.append(
                TmbArrivalSensor(
                    coordinator,
                    entry,
                    item,
                    destination,
                    None if single_destination else destination,
                )
            )

    async_add_entities(entities)


class TmbArrivalSensor(CoordinatorEntity[TmbCoordinator], SensorEntity):
    """Minutes remaining until the next arrival to one destination."""

    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_attribution = "Data provided by Transports Metropolitans de Barcelona"

    def __init__(
        self,
        coordinator: TmbCoordinator,
        entry: ConfigEntry,
        item: dict,
        destination: str,
        direction_label: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._item_key = item_key(item)
        self._destination = destination
        self._mode = item[CONF_MODE]
        self._line_code = item[CONF_LINE_CODE]
        self._line_name = item[CONF_LINE_NAME]
        self._stop_code = item[CONF_STOP_CODE]
        self._stop_name = item[CONF_STOP_NAME]
        self._direction_label = direction_label

        self._attr_icon = "mdi:bus-clock" if self._mode == MODE_BUS else "mdi:subway-variant"
        base_name = f"TMB {self._line_name} - {self._stop_name}"
        self._attr_name = (
            f"{base_name} → {direction_label}" if direction_label else base_name
        )
        self._attr_unique_id = f"{entry.entry_id}_{self._item_key}_{_slugify(destination)}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="TMB",
            manufacturer=MANUFACTURER,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _upcoming(self) -> list[dict]:
        return self.coordinator.data.get(self._item_key, {}).get(self._destination, [])

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
            ATTR_DESTINATION: self._destination,
            ATTR_STOP_NAME: self._stop_name,
            ATTR_STOP_CODE: self._stop_code,
        }
        upcoming = self._upcoming
        if not upcoming:
            return attrs
        attrs[ATTR_NEXT_ARRIVAL] = max(0, upcoming[0]["eta_sec"] // 60)
        attrs[ATTR_UPCOMING] = [
            max(0, arrival["eta_sec"] // 60) for arrival in upcoming[1:]
        ]
        return attrs
