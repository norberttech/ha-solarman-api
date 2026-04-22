"""DataUpdateCoordinator for the Solarman Open API."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    SolarmanApiError,
    SolarmanAuthError,
    SolarmanClient,
    SolarmanRateLimitError,
)
from .const import DEFAULT_UPDATE_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

_COLLECTION_TIME_KEY = "_collectionTime"


class SolarmanCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Polls `currentData` for every known device at a configurable interval."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: SolarmanClient,
        station_id: int,
        devices: list[dict[str, Any]],
        update_interval: timedelta | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{station_id}",
            update_interval=update_interval or DEFAULT_UPDATE_INTERVAL,
        )
        self.client = client
        self.station_id = station_id
        self.devices = devices

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        tasks = [
            self.client.current_data(device["deviceSn"]) for device in self.devices
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        new_data: dict[str, dict[str, Any]] = {}
        previous: dict[str, dict[str, Any]] = self.data or {}

        for device, result in zip(self.devices, results, strict=True):
            sn = device["deviceSn"]
            collection_time = device.get("collectionTime")
            if isinstance(result, SolarmanAuthError):
                raise ConfigEntryAuthFailed(str(result)) from result
            if isinstance(
                result, (SolarmanRateLimitError, SolarmanApiError, asyncio.TimeoutError)
            ):
                _LOGGER.warning("currentData failed for %s: %s", sn, result)
                new_data[sn] = previous.get(sn, {_COLLECTION_TIME_KEY: collection_time})
                continue
            if isinstance(result, BaseException):
                raise UpdateFailed(f"Unexpected error for {sn}: {result}") from result

            parsed: dict[str, Any] = {_COLLECTION_TIME_KEY: collection_time}
            for item in result:
                key = item.get("key")
                if key is None:
                    continue
                parsed[str(key)] = item.get("value")
            new_data[sn] = parsed

        return new_data
