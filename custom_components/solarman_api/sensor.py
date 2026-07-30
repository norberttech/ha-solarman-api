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
    DERIVED_KEY_PREFIX,
    DERIVED_SELF_CONSUMPTION_POWER,
    DERIVED_SELF_CONSUMPTION_TOTAL,
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
        if (
            desc.device_class in _NUMERIC_DEVICE_CLASSES
            or desc.native_unit_of_measurement
        ):
            value = _to_float(raw, desc.suggested_display_precision)
            if value is None or not desc.invert:
                return value
            # Subtracting from 0.0 rather than negating keeps a zero reading
            # as 0.0 instead of -0.0, which would render as "-0" in the UI.
            return 0.0 - value
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
        key = self.entity_description.key
        if key.startswith(DERIVED_KEY_PREFIX):
            return _derive_value(key, bucket)
        return bucket.get(key)


def _to_float(raw: Any, precision: int | None) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if precision is not None:
        return round(value, precision)
    return value


def _as_float(value: Any) -> float | None:
    """Parse a bucket entry to float, returning None on failure."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _derive_value(key: str, bucket: dict[str, Any]) -> float | None:
    """Compute a derived sensor value from other entries in the bucket.

    Returns None when any required input is missing so the sensor becomes
    `unknown` rather than reporting a wrong number. Sign conventions were
    verified empirically against a full day of 5-minute samples covering
    night-battery, sunrise-ramp, solar-peak-export, and idle states.
    """
    if key == DERIVED_SELF_CONSUMPTION_POWER:
        # Instantaneous self-consumption via energy balance at the inverter
        # boundary: inflows (PV + grid_import + battery_discharge) minus
        # outflows (home + grid_export + battery_charge) equals the power
        # the inverter's own electronics/cooling/conversion are burning.
        #
        # Sign conventions from empirical verification. These are the *raw*
        # API polarities — the grid and battery entities publish PG_Pt1 and
        # B_P1 negated (`invert=True`) to match Home Assistant, but that
        # happens at the entity boundary and never reaches this bucket:
        #   TPG    — always ≥ 0, sum of DP1..DPn (more reliable than PVTP
        #            which zeroes out at low power).
        #   PG_Pt1 — + = export, - = import.
        #   B_P1   — + = charge, - = discharge.
        pv = _as_float(bucket.get("TPG"))
        grid = _as_float(bucket.get("PG_Pt1"))
        battery = _as_float(bucket.get("B_P1"))
        home = _as_float(bucket.get("E_Puse_t1"))
        if None in (pv, grid, battery, home):
            return None
        grid_in = max(0.0, -grid)
        grid_out = max(0.0, grid)
        battery_in = max(0.0, -battery)
        battery_out = max(0.0, battery)
        return pv + grid_in + battery_in - home - grid_out - battery_out

    if key == DERIVED_SELF_CONSUMPTION_TOTAL:
        # Lifetime self-consumption from the same balance on cumulative
        # kWh counters. Clamped to >= 0: per-poll rounding can occasionally
        # push this a few Wh negative, which would otherwise trip HA's
        # TOTAL_INCREASING "reset detected" branch and dump the running
        # total as a spike.
        pv_total = _as_float(bucket.get("Et_ge0"))
        grid_import = _as_float(bucket.get("Et_pu1"))
        battery_discharge = _as_float(bucket.get("t_dcg_n1"))
        home_total = _as_float(bucket.get("Et_use1"))
        grid_export = _as_float(bucket.get("t_gc1"))
        battery_charge = _as_float(bucket.get("t_cg_n1"))
        required = (
            pv_total,
            grid_import,
            battery_discharge,
            home_total,
            grid_export,
            battery_charge,
        )
        if None in required:
            return None
        value = (pv_total + grid_import + battery_discharge) - (
            home_total + grid_export + battery_charge
        )
        return max(0.0, value)

    return None
