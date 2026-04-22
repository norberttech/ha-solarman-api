"""Config flow for Solarman Open API."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SolarmanApiError, SolarmanAuthError, SolarmanClient
from .const import (
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
    MAX_UPDATE_INTERVAL_MINUTES,
    MIN_UPDATE_INTERVAL_MINUTES,
)

_LOGGER = logging.getLogger(__name__)

_INTERVAL_VALIDATOR = vol.All(
    vol.Coerce(int),
    vol.Range(min=MIN_UPDATE_INTERVAL_MINUTES, max=MAX_UPDATE_INTERVAL_MINUTES),
)

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_APP_ID): str,
        vol.Required(CONF_APP_SECRET): str,
        vol.Required(
            CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL_MINUTES
        ): _INTERVAL_VALIDATOR,
    }
)

_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


class SolarmanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Solarman."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry_data: Mapping[str, Any] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "SolarmanOptionsFlow":
        return SolarmanOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, station_name = await self._validate(user_input)
            if not errors:
                email = user_input[CONF_EMAIL].lower()
                await self.async_set_unique_id(email)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Solarman ({station_name})",
                    data=user_input,
                )
        return self.async_show_form(
            step_id="user", data_schema=_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        self._reauth_entry_data = entry_data
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._reauth_entry_data is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            merged = {
                **self._reauth_entry_data,
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            errors, _ = await self._validate(merged)
            if not errors:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data=merged
                )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=_REAUTH_SCHEMA, errors=errors
        )

    # self._get_reauth_entry is provided by ConfigFlow since 2024.8;
    # left as-is to rely on the base implementation.

    async def _validate(self, data: Mapping[str, Any]) -> tuple[dict[str, str], str]:
        session = async_get_clientsession(self.hass)
        client = SolarmanClient(
            session=session,
            app_id=data[CONF_APP_ID],
            app_secret=data[CONF_APP_SECRET],
            email=data[CONF_EMAIL],
            password=data[CONF_PASSWORD],
        )
        try:
            await client.authenticate()
            stations = await client.list_stations()
        except SolarmanAuthError:
            return {"base": "invalid_auth"}, ""
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return {"base": "cannot_connect"}, ""
        except SolarmanApiError as err:
            _LOGGER.debug("Solarman API error during config flow: %s", err)
            return {"base": "cannot_connect"}, ""
        except Exception:
            _LOGGER.exception("Unexpected error validating Solarman credentials")
            return {"base": "unknown"}, ""

        if not stations:
            return {"base": "no_stations"}, ""
        station_name = str(
            stations[0].get("name") or f"Station {stations[0].get('id')}"
        )
        return {}, station_name


class SolarmanOptionsFlow(OptionsFlow):
    """Options flow for the update interval."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = self.config_entry.options.get(
            CONF_UPDATE_INTERVAL,
            self.config_entry.data.get(
                CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_MINUTES
            ),
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_UPDATE_INTERVAL, default=current
                ): _INTERVAL_VALIDATOR,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
