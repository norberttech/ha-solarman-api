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
            {
                "collectionTime": 1_700_000_000,
                "dataList": [{"key": "PVTP", "value": "123"}],
            },
            {
                "collectionTime": 1_700_000_005,
                "dataList": [{"key": "SOC_BAP1", "value": "87"}],
            },
        ]
    )
    coordinator = SolarmanCoordinator(hass, _entry(hass), client, 1, _devices())
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.data["SN_INV"]["PVTP"] == "123"
    assert coordinator.data["SN_BAT"]["SOC_BAP1"] == "87"
    assert coordinator.data["SN_INV"]["_collectionTime"] == 1_700_000_000
    assert coordinator.data["SN_BAT"]["_collectionTime"] == 1_700_000_005


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
            {
                "collectionTime": 1_700_000_123,
                "dataList": [{"key": "SOC_BAP1", "value": "87"}],
            },
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


async def test_first_refresh_with_api_error_omits_device(
    hass: HomeAssistant,
) -> None:
    """Cold start + transient API error: device must be omitted from
    `coordinator.data` (not stored as a `{_collectionTime: None}`
    placeholder) so sensors render Unavailable, not Unknown.
    """
    client = MagicMock()
    client.current_data = AsyncMock(
        side_effect=[
            SolarmanApiError(0, "3501004: remote rpc exception"),
            {
                "collectionTime": 1_700_000_000,
                "dataList": [{"key": "SOC_BAP1", "value": "87"}],
            },
        ]
    )
    coordinator = SolarmanCoordinator(hass, _entry(hass), client, 1, _devices())
    data = await coordinator._async_update_data()
    assert "SN_INV" not in data
    assert data["SN_BAT"]["SOC_BAP1"] == "87"


async def test_rate_limit_error_surfaces_as_warning(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    client = MagicMock()
    client.current_data = AsyncMock(
        side_effect=[
            SolarmanRateLimitError(retry_after=10),
            {
                "collectionTime": 1_700_000_456,
                "dataList": [{"key": "SOC_BAP1", "value": "87"}],
            },
        ]
    )
    coordinator = SolarmanCoordinator(hass, _entry(hass), client, 1, _devices())
    with caplog.at_level(
        logging.WARNING, logger="custom_components.solarman_api.coordinator"
    ):
        data = await coordinator._async_update_data()
    assert "SN_INV" in caplog.text
    assert data["SN_BAT"]["SOC_BAP1"] == "87"


async def test_lifetime_counter_zero_replaced_with_previous_positive_value(
    hass: HomeAssistant,
) -> None:
    """Inverter occasionally reports 0 kWh for Et_ge0 when briefly offline.

    Publishing that 0 to HA causes the recorder's TOTAL_INCREASING compile
    to treat the drop as a reset, producing huge negative bars in the Energy
    dashboard. The coordinator should suppress the zero if we have a
    previous positive reading.
    """
    client = MagicMock()
    client.current_data = AsyncMock(
        side_effect=[
            {
                "collectionTime": 1_700_000_000,
                "dataList": [
                    {"key": "Et_ge0", "value": "0"},
                    {"key": "Etdy_ge1", "value": "0"},
                ],
            },
            {
                "collectionTime": 1_700_000_005,
                "dataList": [],
            },
        ]
    )
    coordinator = SolarmanCoordinator(hass, _entry(hass), client, 1, _devices())
    coordinator.data = {
        "SN_INV": {
            "_collectionTime": 1,
            "Et_ge0": "3161.01",
            "Etdy_ge1": "13.87",
        },
        "SN_BAT": {"_collectionTime": 1},
    }
    data = await coordinator._async_update_data()
    # Et_ge0 (lifetime) preserved; Etdy_ge1 (daily, can reset to 0) not kept.
    assert data["SN_INV"]["Et_ge0"] == "3161.01"
    assert data["SN_INV"]["Etdy_ge1"] == "0"


async def test_collection_time_uses_response_not_startup_value(
    hass: HomeAssistant,
) -> None:
    """collectionTime in the per-device bucket must come from each API
    response, not the value captured in the device list at startup. Freezing
    it at startup made every sensor flip Unavailable once STALE_AFTER elapsed.
    """
    client = MagicMock()
    client.current_data = AsyncMock(
        side_effect=[
            {"collectionTime": 1_800_000_000, "dataList": []},
            {"collectionTime": 1_800_000_300, "dataList": []},
        ]
    )
    coordinator = SolarmanCoordinator(hass, _entry(hass), client, 1, _devices())
    await coordinator.async_refresh()

    # device list had collectionTime=1_700_000_000; response wins.
    assert coordinator.data["SN_INV"]["_collectionTime"] == 1_800_000_000
    assert coordinator.data["SN_BAT"]["_collectionTime"] == 1_800_000_300
