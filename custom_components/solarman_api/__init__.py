"""The Solarman Open API integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from datetime import date, timedelta

import voluptuous as vol

from homeassistant.core import ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv

from .api import SolarmanApiError, SolarmanAuthError, SolarmanClient
from .const import (
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
)

SERVICE_FETCH_HISTORICAL = "fetch_historical"
_FETCH_HISTORICAL_SCHEMA = vol.Schema(
    {
        vol.Optional("device_sn"): cv.string,
        vol.Required("start"): cv.date,
        vol.Required("end"): cv.date,
        vol.Optional("time_type", default=2): vol.All(
            vol.Coerce(int), vol.In([1, 2, 3])
        ),
    }
)

SERVICE_IMPORT_HISTORICAL_STATISTICS = "import_historical_statistics"
_IMPORT_STATS_SCHEMA = vol.Schema(
    {
        vol.Optional("days", default=180): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=365)
        ),
        vol.Optional("end"): cv.date,
        vol.Optional("device_sn"): cv.string,
    }
)

PLATFORMS: list[Platform] = [Platform.SENSOR]


@dataclass
class SolarmanRuntimeData:
    """Data stored on the ConfigEntry at runtime."""

    client: SolarmanClient
    coordinator: Any
    station_id: int
    station_name: str
    devices: list[dict[str, Any]]


type SolarmanConfigEntry = ConfigEntry[SolarmanRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: SolarmanConfigEntry) -> bool:
    """Set up Solarman from a config entry."""
    session = async_get_clientsession(hass)
    client = SolarmanClient(
        session=session,
        app_id=entry.data[CONF_APP_ID],
        app_secret=entry.data[CONF_APP_SECRET],
        email=entry.data[CONF_EMAIL],
        password=entry.data[CONF_PASSWORD],
    )

    try:
        await client.authenticate()
        stations = await client.list_stations()
    except SolarmanAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except SolarmanApiError as err:
        raise ConfigEntryNotReady(str(err)) from err

    if not stations:
        raise ConfigEntryNotReady("No stations reported for this account")

    station = stations[0]
    station_id = int(station["id"])
    station_name = str(station.get("name") or f"Station {station_id}")

    try:
        devices = await client.list_devices(station_id)
    except SolarmanAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except SolarmanApiError as err:
        raise ConfigEntryNotReady(str(err)) from err

    from .coordinator import SolarmanCoordinator

    interval_minutes = entry.options.get(
        CONF_UPDATE_INTERVAL,
        entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES),
    )
    coordinator = SolarmanCoordinator(
        hass,
        entry,
        client,
        station_id,
        devices,
        update_interval=timedelta(minutes=int(interval_minutes)),
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = SolarmanRuntimeData(
        client=client,
        coordinator=coordinator,
        station_id=station_id,
        station_name=station_name,
        devices=devices,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SolarmanConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: SolarmanConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration-wide services. Idempotent across config entries."""
    if hass.services.has_service(DOMAIN, SERVICE_FETCH_HISTORICAL):
        return

    async def _fetch_historical(call: ServiceCall) -> dict[str, Any]:
        start = call.data["start"].isoformat()
        end = call.data["end"].isoformat()
        time_type = call.data["time_type"]
        requested_sn = call.data.get("device_sn")

        # Pick a client from any loaded entry; fall back to error if none.
        entries = hass.config_entries.async_entries(DOMAIN)
        loaded = [e for e in entries if getattr(e, "runtime_data", None)]
        if not loaded:
            raise HomeAssistantError("Solarman integration is not loaded")
        runtime = loaded[0].runtime_data
        client: SolarmanClient = runtime.client

        if requested_sn:
            targets = [d for d in runtime.devices if d["deviceSn"] == requested_sn]
            if not targets:
                raise HomeAssistantError(f"Unknown device_sn: {requested_sn}")
        else:
            targets = [
                d
                for d in runtime.devices
                if d.get("deviceType") in ("INVERTER", "BATTERY")
            ]

        result: dict[str, Any] = {}
        for device in targets:
            sn = device["deviceSn"]
            try:
                result[sn] = await client.historical(sn, start, end, time_type)
            except SolarmanApiError as err:
                result[sn] = {"error": str(err)}
        return result

    hass.services.async_register(
        DOMAIN,
        SERVICE_FETCH_HISTORICAL,
        _fetch_historical,
        schema=_FETCH_HISTORICAL_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    async def _import_historical_statistics(call: ServiceCall) -> dict[str, Any]:
        days: int = call.data["days"]
        end: date = call.data.get("end") or (date.today() - timedelta(days=1))
        start: date = end - timedelta(days=days - 1)
        requested_sn = call.data.get("device_sn")

        entries = hass.config_entries.async_entries(DOMAIN)
        loaded = [e for e in entries if getattr(e, "runtime_data", None)]
        if not loaded:
            raise HomeAssistantError("Solarman integration is not loaded")
        runtime = loaded[0].runtime_data

        from .statistics import async_import_historical_statistics

        return await async_import_historical_statistics(
            hass,
            runtime.client,
            runtime.devices,
            start_date=start,
            end_date=end,
            target_device_sn=requested_sn,
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_IMPORT_HISTORICAL_STATISTICS,
        _import_historical_statistics,
        schema=_IMPORT_STATS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
