"""Tests for the Solarman DataUpdateCoordinator."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.core import HomeAssistant

from custom_components.solarman_api.api import (
    SolarmanApiError,
    SolarmanAuthError,
    SolarmanRateLimitError,
)
from custom_components.solarman_api.const import DOMAIN
from custom_components.solarman_api.coordinator import SolarmanCoordinator

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _devices() -> list[dict[str, Any]]:
    return [
        {
            "deviceSn": "SN_INV",
            "deviceType": "INVERTER",
            "collectionTime": 1_700_000_000,
        },
        {
            "deviceSn": "SN_BAT",
            "deviceType": "BATTERY",
            "collectionTime": 1_700_000_000,
        },
    ]


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id="u@e")
    entry.add_to_hass(hass)
    return entry


async def test_first_refresh_populates_data(hass: HomeAssistant) -> None:
    client = MagicMock()
    client.current_data = AsyncMock(
        side_effect=[
            [{"key": "PVTP", "value": "123"}],
            [{"key": "SOC_BAP1", "value": "87"}],
        ]
    )
    coordinator = SolarmanCoordinator(hass, _entry(hass), client, 1, _devices())
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.data["SN_INV"]["PVTP"] == "123"
    assert coordinator.data["SN_BAT"]["SOC_BAP1"] == "87"
    assert coordinator.data["SN_INV"]["_collectionTime"] == 1_700_000_000


async def test_auth_error_raises_config_entry_auth_failed(hass: HomeAssistant) -> None:
    client = MagicMock()
    client.current_data = AsyncMock(side_effect=SolarmanAuthError("bad token"))
    coordinator = SolarmanCoordinator(hass, _entry(hass), client, 1, _devices())
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_per_device_api_error_does_not_blackout_others(
    hass: HomeAssistant,
) -> None:
    client = MagicMock()
    client.current_data = AsyncMock(
        side_effect=[
            SolarmanApiError(500, "boom"),
            [{"key": "SOC_BAP1", "value": "87"}],
        ]
    )
    coordinator = SolarmanCoordinator(hass, _entry(hass), client, 1, _devices())
    coordinator.data = {
        "SN_INV": {"_collectionTime": 1, "PVTP": "previous"},
        "SN_BAT": {},
    }
    data = await coordinator._async_update_data()
    # failing device reuses previous bucket, other is populated
    assert data["SN_INV"]["PVTP"] == "previous"
    assert data["SN_BAT"]["SOC_BAP1"] == "87"


async def test_rate_limit_error_surfaces_as_warning(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    client = MagicMock()
    client.current_data = AsyncMock(
        side_effect=[
            SolarmanRateLimitError(retry_after=10),
            [{"key": "SOC_BAP1", "value": "87"}],
        ]
    )
    coordinator = SolarmanCoordinator(hass, _entry(hass), client, 1, _devices())
    with caplog.at_level(
        logging.WARNING, logger="custom_components.solarman_api.coordinator"
    ):
        data = await coordinator._async_update_data()
    assert "SN_INV" in caplog.text
    assert data["SN_BAT"]["SOC_BAP1"] == "87"
