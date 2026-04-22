"""Constants and sensor descriptors for the Solarman Open API integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Final, Literal

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.helpers.entity import EntityCategory

DOMAIN: Final = "solarman_api"
BASE_URL: Final = "https://globalapi.solarmanpv.com"

DEFAULT_UPDATE_INTERVAL: Final = timedelta(minutes=5)
STALE_AFTER: Final = timedelta(hours=1)
REQUEST_TIMEOUT: Final = 30

MIN_UPDATE_INTERVAL_MINUTES: Final = 5
MAX_UPDATE_INTERVAL_MINUTES: Final = 60
DEFAULT_UPDATE_INTERVAL_MINUTES: Final = 5

DEFAULT_HEADERS: Final = {"Content-Type": "application/json"}

CONF_APP_ID: Final = "app_id"
CONF_APP_SECRET: Final = "app_secret"
CONF_UPDATE_INTERVAL: Final = "update_interval_minutes"

DeviceTypeLiteral = Literal["INVERTER", "BATTERY", "COLLECTOR"]


@dataclass(frozen=True, kw_only=True)
class SolarmanSensorDescription(SensorEntityDescription):
    """Sensor descriptor with the owning device type annotation."""

    device_type: DeviceTypeLiteral = "INVERTER"


_POWER_W = dict(
    native_unit_of_measurement=UnitOfPower.WATT,
    device_class=SensorDeviceClass.POWER,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=0,
)
_ENERGY_KWH_TOTAL = dict(
    native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    device_class=SensorDeviceClass.ENERGY,
    state_class=SensorStateClass.TOTAL_INCREASING,
    suggested_display_precision=2,
)
_VOLTAGE_V = dict(
    native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    device_class=SensorDeviceClass.VOLTAGE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=1,
)
_CURRENT_A = dict(
    native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
    device_class=SensorDeviceClass.CURRENT,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=2,
)
_TEMPERATURE_C = dict(
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    device_class=SensorDeviceClass.TEMPERATURE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=1,
)


INVERTER_SENSORS: Final[tuple[SolarmanSensorDescription, ...]] = (
    SolarmanSensorDescription(
        key="Et_ge0",
        translation_key="solar_production_total",
        name="Solar production total",
        **_ENERGY_KWH_TOTAL,
    ),
    SolarmanSensorDescription(
        key="Etdy_ge1",
        translation_key="solar_production_today",
        name="Solar production today",
        **_ENERGY_KWH_TOTAL,
    ),
    SolarmanSensorDescription(
        key="PVTP",
        translation_key="pv_power",
        name="PV power",
        **_POWER_W,
    ),
    SolarmanSensorDescription(
        key="DP1",
        translation_key="pv1_power",
        name="PV1 power",
        **_POWER_W,
    ),
    SolarmanSensorDescription(
        key="DP2",
        translation_key="pv2_power",
        name="PV2 power",
        **_POWER_W,
    ),
    SolarmanSensorDescription(
        key="T_AC_OP",
        translation_key="ac_output_power",
        name="AC output power",
        **_POWER_W,
    ),
    SolarmanSensorDescription(
        key="PG_Pt1",
        translation_key="grid_power",
        name="Grid power",
        **_POWER_W,
    ),
    SolarmanSensorDescription(
        key="t_gc1",
        translation_key="grid_export_total",
        name="Grid export total",
        **_ENERGY_KWH_TOTAL,
    ),
    SolarmanSensorDescription(
        key="t_gc_tdy1",
        translation_key="grid_export_today",
        name="Grid export today",
        **_ENERGY_KWH_TOTAL,
    ),
    SolarmanSensorDescription(
        key="Et_pu1",
        translation_key="grid_import_total",
        name="Grid import total",
        **_ENERGY_KWH_TOTAL,
    ),
    SolarmanSensorDescription(
        key="Etdy_pu1",
        translation_key="grid_import_today",
        name="Grid import today",
        **_ENERGY_KWH_TOTAL,
    ),
    SolarmanSensorDescription(
        key="E_Puse_t1",
        translation_key="home_consumption_power",
        name="Home consumption power",
        **_POWER_W,
    ),
    SolarmanSensorDescription(
        key="Et_use1",
        translation_key="home_consumption_total",
        name="Home consumption total",
        **_ENERGY_KWH_TOTAL,
    ),
    SolarmanSensorDescription(
        key="Etdy_use1",
        translation_key="home_consumption_today",
        name="Home consumption today",
        **_ENERGY_KWH_TOTAL,
    ),
    SolarmanSensorDescription(
        key="B_P1",
        translation_key="battery_power",
        name="Battery power",
        **_POWER_W,
    ),
    SolarmanSensorDescription(
        key="B_left_cap1",
        translation_key="battery_soc",
        name="Battery SOC",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    SolarmanSensorDescription(
        key="t_cg_n1",
        translation_key="battery_charge_total",
        name="Battery charge total",
        **_ENERGY_KWH_TOTAL,
    ),
    SolarmanSensorDescription(
        key="Etdy_cg1",
        translation_key="battery_charge_today",
        name="Battery charge today",
        **_ENERGY_KWH_TOTAL,
    ),
    SolarmanSensorDescription(
        key="t_dcg_n1",
        translation_key="battery_discharge_total",
        name="Battery discharge total",
        **_ENERGY_KWH_TOTAL,
    ),
    SolarmanSensorDescription(
        key="Etdy_dcg1",
        translation_key="battery_discharge_today",
        name="Battery discharge today",
        **_ENERGY_KWH_TOTAL,
    ),
    SolarmanSensorDescription(
        key="PG_F1",
        translation_key="grid_frequency",
        name="Grid frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    # ----- DIAGNOSTIC: per-phase voltages -----
    SolarmanSensorDescription(
        key="AV1",
        translation_key="phase_1_voltage",
        name="Phase 1 voltage",
        entity_category=EntityCategory.DIAGNOSTIC,
        **_VOLTAGE_V,
    ),
    SolarmanSensorDescription(
        key="AV2",
        translation_key="phase_2_voltage",
        name="Phase 2 voltage",
        entity_category=EntityCategory.DIAGNOSTIC,
        **_VOLTAGE_V,
    ),
    SolarmanSensorDescription(
        key="AV3",
        translation_key="phase_3_voltage",
        name="Phase 3 voltage",
        entity_category=EntityCategory.DIAGNOSTIC,
        **_VOLTAGE_V,
    ),
    # ----- DIAGNOSTIC: per-phase currents -----
    SolarmanSensorDescription(
        key="AC1",
        translation_key="phase_1_current",
        name="Phase 1 current",
        entity_category=EntityCategory.DIAGNOSTIC,
        **_CURRENT_A,
    ),
    SolarmanSensorDescription(
        key="AC2",
        translation_key="phase_2_current",
        name="Phase 2 current",
        entity_category=EntityCategory.DIAGNOSTIC,
        **_CURRENT_A,
    ),
    SolarmanSensorDescription(
        key="AC3",
        translation_key="phase_3_current",
        name="Phase 3 current",
        entity_category=EntityCategory.DIAGNOSTIC,
        **_CURRENT_A,
    ),
    # ----- DIAGNOSTIC: per-phase powers -----
    SolarmanSensorDescription(
        key="AP1",
        translation_key="phase_1_power",
        name="Phase 1 power",
        entity_category=EntityCategory.DIAGNOSTIC,
        **_POWER_W,
    ),
    SolarmanSensorDescription(
        key="AP2",
        translation_key="phase_2_power",
        name="Phase 2 power",
        entity_category=EntityCategory.DIAGNOSTIC,
        **_POWER_W,
    ),
    SolarmanSensorDescription(
        key="AP3",
        translation_key="phase_3_power",
        name="Phase 3 power",
        entity_category=EntityCategory.DIAGNOSTIC,
        **_POWER_W,
    ),
    # ----- DIAGNOSTIC: status text -----
    SolarmanSensorDescription(
        key="INV_ST1",
        translation_key="inverter_status",
        name="Inverter status",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SolarmanSensorDescription(
        key="ST_PG1",
        translation_key="grid_status",
        name="Grid status",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # ----- DIAGNOSTIC: temperatures -----
    SolarmanSensorDescription(
        key="T_MDU1",
        translation_key="module_temperature",
        name="Module temperature",
        entity_category=EntityCategory.DIAGNOSTIC,
        **_TEMPERATURE_C,
    ),
    SolarmanSensorDescription(
        key="SPAT",
        translation_key="ambient_temperature",
        name="Ambient temperature",
        entity_category=EntityCategory.DIAGNOSTIC,
        **_TEMPERATURE_C,
    ),
    SolarmanSensorDescription(
        key="T_RDT2",
        translation_key="radiator_temperature",
        name="Radiator temperature",
        entity_category=EntityCategory.DIAGNOSTIC,
        **_TEMPERATURE_C,
    ),
    # ----- DIAGNOSTIC: insulation / DC bus (unit returned by API; pass-through) -----
    SolarmanSensorDescription(
        key="IPV",
        translation_key="insulation_impedance",
        name="Insulation impedance",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
    ),
    SolarmanSensorDescription(
        key="Bus_V1",
        translation_key="dc_bus_voltage",
        name="DC bus voltage",
        entity_category=EntityCategory.DIAGNOSTIC,
        **_VOLTAGE_V,
    ),
)


BATTERY_SENSORS: Final[tuple[SolarmanSensorDescription, ...]] = (
    SolarmanSensorDescription(
        key="V_BAP1",
        translation_key="battery_pack_voltage",
        name="Pack voltage",
        device_type="BATTERY",
        **_VOLTAGE_V,
    ),
    SolarmanSensorDescription(
        key="I_BAP1",
        translation_key="battery_pack_current",
        name="Pack current",
        device_type="BATTERY",
        **_CURRENT_A,
    ),
    SolarmanSensorDescription(
        key="SOC_BAP1",
        translation_key="battery_pack_soc",
        name="Pack SOC",
        device_type="BATTERY",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    SolarmanSensorDescription(
        key="SOH_BAP1",
        translation_key="battery_pack_soh",
        name="Pack SOH",
        device_type="BATTERY",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    SolarmanSensorDescription(
        key="NUMcyc1",
        translation_key="battery_cycle_count",
        name="Cycle count",
        device_type="BATTERY",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    SolarmanSensorDescription(
        key="INFO_Bbas1",
        translation_key="battery_status",
        name="Status",
        device_type="BATTERY",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Per-module temperatures
    SolarmanSensorDescription(
        key="T_MDU0",
        translation_key="module_0_temperature",
        name="Module 0 temperature",
        device_type="BATTERY",
        entity_category=EntityCategory.DIAGNOSTIC,
        **_TEMPERATURE_C,
    ),
    SolarmanSensorDescription(
        key="T_MDU1",
        translation_key="module_1_temperature",
        name="Module 1 temperature",
        device_type="BATTERY",
        entity_category=EntityCategory.DIAGNOSTIC,
        **_TEMPERATURE_C,
    ),
    SolarmanSensorDescription(
        key="T_MDU2",
        translation_key="module_2_temperature",
        name="Module 2 temperature",
        device_type="BATTERY",
        entity_category=EntityCategory.DIAGNOSTIC,
        **_TEMPERATURE_C,
    ),
    SolarmanSensorDescription(
        key="T_MDU3",
        translation_key="module_3_temperature",
        name="Module 3 temperature",
        device_type="BATTERY",
        entity_category=EntityCategory.DIAGNOSTIC,
        **_TEMPERATURE_C,
    ),
    # Per-module voltages
    SolarmanSensorDescription(
        key="V_MDU0",
        translation_key="module_0_voltage",
        name="Module 0 voltage",
        device_type="BATTERY",
        entity_category=EntityCategory.DIAGNOSTIC,
        **_VOLTAGE_V,
    ),
    SolarmanSensorDescription(
        key="V_MDU1",
        translation_key="module_1_voltage",
        name="Module 1 voltage",
        device_type="BATTERY",
        entity_category=EntityCategory.DIAGNOSTIC,
        **_VOLTAGE_V,
    ),
    SolarmanSensorDescription(
        key="V_MDU2",
        translation_key="module_2_voltage",
        name="Module 2 voltage",
        device_type="BATTERY",
        entity_category=EntityCategory.DIAGNOSTIC,
        **_VOLTAGE_V,
    ),
    SolarmanSensorDescription(
        key="V_MDU3",
        translation_key="module_3_voltage",
        name="Module 3 voltage",
        device_type="BATTERY",
        entity_category=EntityCategory.DIAGNOSTIC,
        **_VOLTAGE_V,
    ),
)


SENSORS_BY_DEVICE_TYPE: Final[dict[str, tuple[SolarmanSensorDescription, ...]]] = {
    "INVERTER": INVERTER_SENSORS,
    "BATTERY": BATTERY_SENSORS,
}
