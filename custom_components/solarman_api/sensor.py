"""Sensor platform for Solarman Open API."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    SENSORS_BY_DEVICE_TYPE,
    STALE_AFTER,
    SolarmanSensorDescription,
)
from .coordinator import SolarmanCoordinator

_COLLECTION_TIME_KEY = "_collectionTime"

_NUMERIC_DEVICE_CLASSES = {
    SensorDeviceClass.POWER,
    SensorDeviceClass.ENERGY,
    SensorDeviceClass.VOLTAGE,
    SensorDeviceClass.CURRENT,
    SensorDeviceClass.FREQUENCY,
    SensorDeviceClass.TEMPERATURE,
    SensorDeviceClass.BATTERY,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Build one SolarmanSensor per (device, descriptor)."""
    runtime = entry.runtime_data
    coordinator: SolarmanCoordinator = runtime.coordinator

    entities: list[SolarmanSensor] = []
    for device in runtime.devices:
        descriptors = SENSORS_BY_DEVICE_TYPE.get(device.get("deviceType", ""))
        if not descriptors:
            continue
        for descriptor in descriptors:
            entities.append(SolarmanSensor(coordinator, device, descriptor))
    async_add_entities(entities)


class SolarmanSensor(CoordinatorEntity[SolarmanCoordinator], SensorEntity):
    """Single Solarman `dataList` key, bound to a device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolarmanCoordinator,
        device: dict[str, Any],
        description: SolarmanSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._device_sn: str = str(device["deviceSn"])
        self._attr_unique_id = f"{self._device_sn}_{description.key}"
        # Manufacturer/model/name fall back gracefully when API omits them.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device_sn)},
            manufacturer=str(
                device.get("manufactor") or device.get("manufacturer") or "Solarman"
            ),
            model=str(
                device.get("deviceModel") or device.get("deviceType") or ""
            ).title()
            or None,
            name=str(device.get("deviceName") or self._device_sn),
            serial_number=self._device_sn,
        )

    @property
    def native_value(self) -> Any:
        raw = self._raw_value()
        if raw is None or raw == "":
            return None
        desc = self.entity_description
        if desc.device_class in _NUMERIC_DEVICE_CLASSES:
            return _to_float(raw, desc.suggested_display_precision)
        if desc.native_unit_of_measurement:
            return _to_float(raw, desc.suggested_display_precision)
        return str(raw)

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        bucket = (self.coordinator.data or {}).get(self._device_sn)
        if bucket is None:
            return False
        collection_time = bucket.get(_COLLECTION_TIME_KEY)
        if collection_time is None:
            return True
        try:
            ts = datetime.fromtimestamp(int(collection_time), tz=timezone.utc)
        except (OverflowError, OSError, TypeError, ValueError):
            return True
        return (datetime.now(tz=timezone.utc) - ts) <= STALE_AFTER

    def _raw_value(self) -> Any:
        bucket = (self.coordinator.data or {}).get(self._device_sn)
        if bucket is None:
            return None
        return bucket.get(self.entity_description.key)


def _to_float(raw: Any, precision: int | None) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if precision is not None:
        return round(value, precision)
    return value
