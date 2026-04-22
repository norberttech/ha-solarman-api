"""Tests for the Solarman config flow."""

from __future__ import annotations

import re
from typing import Any

import pytest
from aioresponses import aioresponses
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries, data_entry_flow
from homeassistant.data_entry_flow import InvalidData
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from custom_components.solarman_api.const import (
    BASE_URL,
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

TOKEN_URL = re.compile(rf"^{re.escape(BASE_URL)}/account/v1\.0/token.*")
STATIONS_URL = f"{BASE_URL}/station/v1.0/list"
DEVICES_URL = f"{BASE_URL}/station/v1.0/device"

_VALID_INPUT: dict[str, Any] = {
    CONF_EMAIL: "user@example.com",
    CONF_PASSWORD: "secret",
    CONF_APP_ID: "app",
    CONF_APP_SECRET: "app-secret",
    CONF_UPDATE_INTERVAL: 5,
}


async def _start_user_flow(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    return result["flow_id"]


async def test_user_flow_success(hass: HomeAssistant) -> None:
    flow_id = await _start_user_flow(hass)
    with aioresponses() as m:
        m.post(TOKEN_URL, payload={"success": True, "access_token": "tok"})
        m.post(
            STATIONS_URL,
            payload={"success": True, "stationList": [{"id": 1473532, "name": "Home"}]},
        )
        result = await hass.config_entries.flow.async_configure(flow_id, _VALID_INPUT)

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "Solarman (Home)"
    assert result["data"] == _VALID_INPUT


async def test_user_flow_invalid_auth(hass: HomeAssistant) -> None:
    flow_id = await _start_user_flow(hass)
    with aioresponses() as m:
        m.post(TOKEN_URL, payload={"success": False, "msg": "nope"})
        result = await hass.config_entries.flow.async_configure(flow_id, _VALID_INPUT)
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(hass: HomeAssistant) -> None:
    import aiohttp

    flow_id = await _start_user_flow(hass)
    with aioresponses() as m:
        m.post(TOKEN_URL, exception=aiohttp.ClientError("boom"))
        result = await hass.config_entries.flow.async_configure(flow_id, _VALID_INPUT)
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_no_stations(hass: HomeAssistant) -> None:
    flow_id = await _start_user_flow(hass)
    with aioresponses() as m:
        m.post(TOKEN_URL, payload={"success": True, "access_token": "tok"})
        m.post(STATIONS_URL, payload={"success": True, "stationList": []})
        result = await hass.config_entries.flow.async_configure(flow_id, _VALID_INPUT)
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "no_stations"}


async def test_user_flow_already_configured(hass: HomeAssistant) -> None:
    existing = MockConfigEntry(
        domain=DOMAIN,
        data=_VALID_INPUT,
        unique_id="user@example.com",
    )
    existing.add_to_hass(hass)

    flow_id = await _start_user_flow(hass)
    with aioresponses() as m:
        m.post(TOKEN_URL, payload={"success": True, "access_token": "tok"})
        m.post(
            STATIONS_URL,
            payload={"success": True, "stationList": [{"id": 1, "name": "H"}]},
        )
        result = await hass.config_entries.flow.async_configure(flow_id, _VALID_INPUT)
    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_flow_success(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**_VALID_INPUT, CONF_PASSWORD: "old-password"},
        unique_id="user@example.com",
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with aioresponses() as m:
        m.post(TOKEN_URL, payload={"success": True, "access_token": "tok"})
        m.post(
            STATIONS_URL,
            payload={"success": True, "stationList": [{"id": 1, "name": "H"}]},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "new-password"}
        )

    assert result["type"] == data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-password"


async def test_user_flow_rejects_interval_below_minimum(hass: HomeAssistant) -> None:
    flow_id = await _start_user_flow(hass)
    bad_input = {**_VALID_INPUT, CONF_UPDATE_INTERVAL: 1}
    with aioresponses():
        with pytest.raises(InvalidData):
            await hass.config_entries.flow.async_configure(flow_id, bad_input)


async def test_options_flow_updates_interval(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_VALID_INPUT,
        unique_id="user@example.com",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_UPDATE_INTERVAL: 15}
    )
    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_UPDATE_INTERVAL: 15}


async def test_options_flow_rejects_out_of_range(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=_VALID_INPUT,
        unique_id="user@example.com",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_UPDATE_INTERVAL: 120}
        )
