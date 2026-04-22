"""DataUpdateCoordinator for the Solarman Open API."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from time import time
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
from .const import DEFAULT_UPDATE_INTERVAL, DOMAIN, LIFETIME_CUMULATIVE_KEYS

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
            if isinstance(result, SolarmanAuthError):
                raise ConfigEntryAuthFailed(str(result)) from result
            if isinstance(
                result, (SolarmanRateLimitError, SolarmanApiError, asyncio.TimeoutError)
            ):
                _LOGGER.warning("currentData failed for %s: %s", sn, result)
                new_data[sn] = previous.get(sn, {_COLLECTION_TIME_KEY: None})
                continue
            if isinstance(result, BaseException):
                raise UpdateFailed(f"Unexpected error for {sn}: {result}") from result

            # Prefer the response's own collectionTime (the inverter push
            # timestamp). Fall back to wall-clock now() so the freshness
            # check in the sensor's `available` property doesn't go stale
            # when Solarman omits the field.
            response_collection_time = (
                result.get("collectionTime") if isinstance(result, dict) else None
            )
            collection_time: int
            try:
                collection_time = int(response_collection_time)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                collection_time = int(time())

            data_list = (
                result.get("dataList") if isinstance(result, dict) else None
            ) or []
            parsed: dict[str, Any] = {_COLLECTION_TIME_KEY: collection_time}
            for item in data_list:
                key = item.get("key")
                if key is None:
                    continue
                parsed[str(key)] = item.get("value")

            prev_bucket = previous.get(sn) or {}
            for key in LIFETIME_CUMULATIVE_KEYS:
                if key not in parsed:
                    continue
                if _looks_positive(prev_bucket.get(key)) and _is_zero(parsed[key]):
                    _LOGGER.debug(
                        "Dropping spurious 0 for %s[%s]; keeping previous %s",
                        sn,
                        key,
                        prev_bucket[key],
                    )
                    parsed[key] = prev_bucket[key]

            new_data[sn] = parsed

        return new_data


def _is_zero(value: Any) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _looks_positive(value: Any) -> bool:
    try:
        return float(value) > 0.0
    except (TypeError, ValueError):
        return False
