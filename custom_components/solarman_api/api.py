"""Async HTTP client for the Solarman Open API."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

import aiohttp

from .const import BASE_URL, DEFAULT_HEADERS, REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class SolarmanAuthError(Exception):
    """Raised when authentication fails."""


class SolarmanRateLimitError(Exception):
    """Raised when the API returns 429 twice in a row."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Rate limited; retry after {retry_after}s")
        self.retry_after = retry_after


class SolarmanApiError(Exception):
    """Raised for any other non-2xx response or malformed payload."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Solarman API error ({status}): {body[:200]}")
        self.status = status
        self.body = body


class SolarmanClient:
    """Thin async client around the Solarman Open API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        app_id: str,
        app_secret: str,
        email: str,
        password: str,
    ) -> None:
        self._session = session
        self._app_id = app_id
        self._app_secret = app_secret
        self._email = email
        self._password = password
        self._access_token: str | None = None

    @property
    def access_token(self) -> str | None:
        return self._access_token

    async def authenticate(self) -> str:
        """Obtain a fresh bearer token."""
        payload = {
            "appSecret": self._app_secret,
            "email": self._email,
            "password": hashlib.sha256(self._password.encode()).hexdigest(),
        }
        params = {"appId": self._app_id, "language": "en"}
        data = await self._raw_request(
            "POST",
            "/account/v1.0/token",
            json=payload,
            params=params,
            authed=False,
        )
        if not data.get("success") or not data.get("access_token"):
            raise SolarmanAuthError(
                f"Authentication failed: {data.get('msg') or data.get('code') or 'unknown'}"
            )
        self._access_token = str(data["access_token"])
        return self._access_token

    async def list_stations(self) -> list[dict[str, Any]]:
        """Return the list of stations for the account."""
        data = await self._request(
            "POST", "/station/v1.0/list", json={"page": 1, "size": 20}
        )
        return list(data.get("stationList") or [])

    async def list_devices(self, station_id: int) -> list[dict[str, Any]]:
        """Return the devices registered at a station."""
        data = await self._request(
            "POST", "/station/v1.0/device", json={"stationId": station_id}
        )
        return list(data.get("deviceListItems") or [])

    async def current_data(self, device_sn: str) -> list[dict[str, Any]]:
        """Return the current `dataList` for a single device."""
        data = await self._request(
            "POST", "/device/v1.0/currentData", json={"deviceSn": device_sn}
        )
        return list(data.get("dataList") or [])

    async def historical(
        self,
        device_sn: str,
        start_date: str,
        end_date: str,
        time_type: int = 2,
    ) -> dict[str, Any]:
        """Return historical data for a device over a date range.

        `time_type`: 1 = 5-minute samples within a day, 2 = daily samples,
        3 = monthly samples. `start_date` / `end_date` are ISO dates
        ("YYYY-MM-DD").

        Returns the raw top-level JSON so the caller can adapt to whichever
        list field the API uses (shape is not fully documented upstream).
        """
        return await self._request(
            "POST",
            "/device/v1.0/historical",
            json={
                "deviceSn": device_sn,
                "startTime": start_date,
                "endTime": end_date,
                "timeType": time_type,
            },
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Authenticated request with one-shot 401 reauth and one-shot 429 retry."""
        if self._access_token is None:
            await self.authenticate()
        try:
            return await self._raw_request(
                method, path, json=json, params=params, authed=True
            )
        except _Unauthorized:
            _LOGGER.info("Solarman token rejected; re-authenticating once")
            await self.authenticate()
            try:
                return await self._raw_request(
                    method, path, json=json, params=params, authed=True
                )
            except _Unauthorized as err:
                raise SolarmanAuthError(
                    "Re-authentication did not restore access"
                ) from err
        except _RateLimited as err:
            _LOGGER.warning(
                "Solarman rate-limited; sleeping %ss then retrying", err.retry_after
            )
            await asyncio.sleep(err.retry_after)
            try:
                return await self._raw_request(
                    method, path, json=json, params=params, authed=True
                )
            except _RateLimited as err2:
                raise SolarmanRateLimitError(err2.retry_after) from err2

    async def _raw_request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None,
        params: dict[str, Any] | None = None,
        authed: bool,
    ) -> dict[str, Any]:
        """Single HTTP attempt. Raises _Unauthorized / _RateLimited for retryable states."""
        headers = dict(DEFAULT_HEADERS)
        if authed:
            if self._access_token is None:
                raise SolarmanAuthError("No access token available")
            headers["Authorization"] = f"bearer {self._access_token}"
        url = f"{BASE_URL}{path}"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.request(
                    method, url, json=json, params=params, headers=headers
                ) as response:
                    status = response.status
                    body = await response.text()
                    _LOGGER.debug("Solarman %s %s -> %s", method, path, status)
                    if status == 401:
                        raise _Unauthorized()
                    if status == 429:
                        retry_after = _parse_retry_after(
                            response.headers.get("Retry-After")
                        )
                        raise _RateLimited(retry_after)
                    if status >= 400:
                        raise SolarmanApiError(status, body)
                    try:
                        return await _decode_json(body)
                    except ValueError as err:
                        raise SolarmanApiError(status, body) from err
        except asyncio.TimeoutError as err:
            raise SolarmanApiError(0, f"timeout after {REQUEST_TIMEOUT}s") from err
        except aiohttp.ClientError as err:
            raise SolarmanApiError(0, f"client error: {err}") from err


class _Unauthorized(Exception):
    """Internal marker for a 401 response."""


class _RateLimited(Exception):
    """Internal marker for a 429 response."""

    def __init__(self, retry_after: int) -> None:
        super().__init__()
        self.retry_after = retry_after


def _parse_retry_after(header_value: str | None) -> int:
    if not header_value:
        return 30
    try:
        return max(1, int(float(header_value)))
    except ValueError:
        return 30


async def _decode_json(body: str) -> dict[str, Any]:
    import json as _json

    data = _json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("expected JSON object")
    return data
