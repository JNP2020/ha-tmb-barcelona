"""Sensor platform for TMB."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .alerts import alerts_for_line, line_status, summarize
from .alerts_coordinator import TmbAlertsCoordinator
from .const import (
    ATTR_ALERT_COUNT,
    ATTR_ALERTS,
    ATTR_DESTINATION,
    ATTR_LINE,
    ATTR_LINE_COLOR,
    ATTR_LINE_STATUS,
    ATTR_MODE,
    ATTR_NEXT_ARRIVAL,
    ATTR_SERVICE_STATUS,
    ATTR_STOP_CODE,
    ATTR_STOP_NAME,
    ATTR_UPCOMING,
    ATTRIBUTION,
    CONF_LINE_COLOR,
    CONF_LINE_NAME,
    CONF_MODE,
    CONF_STOP_CODE,
    CONF_STOP_NAME,
    CONF_STOPS,
    DOMAIN,
    MANUFACTURER,
    MODE_BUS,
    MODE_METRO,
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

    alerts_coordinator: TmbAlertsCoordinator | None = data.get("alerts_coordinator")

    entities: list[SensorEntity] = [
        TmbArrivalSensor(coordinator, entry, item, alerts_coordinator)
        for item in entry.options.get(CONF_STOPS, [])
    ]
    if alerts_coordinator is not None:
        entities.append(TmbMetroAlertsSensor(alerts_coordinator, entry))
    async_add_entities(entities)


class TmbArrivalSensor(CoordinatorEntity[TmbCoordinator], SensorEntity):
    """Minutes remaining until the next arrival for one configured line/stop."""

    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_attribution = ATTRIBUTION
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: TmbCoordinator,
        entry: ConfigEntry,
        item: dict,
        alerts_coordinator: TmbAlertsCoordinator | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._alerts_coordinator = alerts_coordinator
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

    async def async_added_to_hass(self) -> None:
        """Also refresh state when the (separate) alerts feed updates."""
        await super().async_added_to_hass()
        if self._alerts_coordinator is not None and self._mode == MODE_METRO:
            self.async_on_remove(
                self._alerts_coordinator.async_add_listener(self.async_write_ha_state)
            )

    @property
    def _line_alerts(self) -> list:
        if self._alerts_coordinator is None or self._mode != MODE_METRO:
            return []
        return alerts_for_line(self._alerts_coordinator.data or [], self._line_name)

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
        if self._alerts_coordinator is not None and self._mode == MODE_METRO:
            line_alerts = self._line_alerts
            attrs[ATTR_LINE_STATUS] = line_status(line_alerts)
            attrs[ATTR_ALERTS] = [summarize(alert) for alert in line_alerts]
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



class TmbMetroAlertsSensor(CoordinatorEntity[TmbAlertsCoordinator], SensorEntity):
    """Number of metro service notices in force; details in attributes."""

    _attr_attribution = ATTRIBUTION
    _attr_icon = "mdi:alert-circle-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "alerts"

    def __init__(self, coordinator: TmbAlertsCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_name = "TMB Metro alerts"
        self._attr_unique_id = f"{entry.entry_id}_metro_alerts"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="TMB",
            manufacturer=MANUFACTURER,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data or [])

    @property
    def extra_state_attributes(self) -> dict:
        alerts = self.coordinator.data or []
        affected = sorted({line for alert in alerts for line in alert["lines"]})
        return {
            ATTR_ALERT_COUNT: len(alerts),
            "affected_lines": affected,
            ATTR_ALERTS: [summarize(alert) for alert in alerts],
        }
