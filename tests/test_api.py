"""Unit tests for custom_components.solarman_api.api."""

from __future__ import annotations

import hashlib
import re

import aiohttp
import pytest
import pytest_asyncio
from aioresponses import aioresponses

from custom_components.solarman_api.api import (
    SolarmanApiError,
    SolarmanAuthError,
    SolarmanClient,
    SolarmanRateLimitError,
)
from custom_components.solarman_api.const import BASE_URL

TOKEN_URL = re.compile(rf"^{re.escape(BASE_URL)}/account/v1\.0/token.*")
CURRENT_URL = f"{BASE_URL}/device/v1.0/currentData"


@pytest_asyncio.fixture
async def session():
    async with aiohttp.ClientSession() as s:
        yield s


def _make_client(session: aiohttp.ClientSession) -> SolarmanClient:
    return SolarmanClient(
        session=session,
        app_id="app",
        app_secret="secret",
        email="user@example.com",
        password="pw123",
    )


@pytest.mark.asyncio
async def test_authenticate_success(session):
    client = _make_client(session)
    with aioresponses() as m:
        m.post(
            TOKEN_URL,
            payload={"success": True, "access_token": "tok", "expires_in": 3600},
        )
        token = await client.authenticate()
        requests = [
            req
            for (method, url), entries in m.requests.items()
            for req in entries
            if method == "POST" and "token" in str(url)
        ]

    assert token == "tok"
    assert client.access_token == "tok"
    assert len(requests) == 1
    sent_json = requests[0].kwargs["json"]
    assert sent_json["password"] == hashlib.sha256(b"pw123").hexdigest()
    assert sent_json["email"] == "user@example.com"
    assert sent_json["appSecret"] == "secret"


@pytest.mark.asyncio
async def test_authenticate_failure(session):
    client = _make_client(session)
    with aioresponses() as m:
        m.post(TOKEN_URL, payload={"success": False, "msg": "bad creds"})
        with pytest.raises(SolarmanAuthError):
            await client.authenticate()


@pytest.mark.asyncio
async def test_current_data_success(session):
    client = _make_client(session)
    with aioresponses() as m:
        m.post(TOKEN_URL, payload={"success": True, "access_token": "tok"})
        m.post(
            CURRENT_URL,
            payload={
                "success": True,
                "dataList": [{"key": "PVTP", "value": "123", "unit": "W"}],
            },
        )
        result = await client.current_data("SN1")
    assert result["dataList"] == [{"key": "PVTP", "value": "123", "unit": "W"}]


@pytest.mark.asyncio
async def test_401_triggers_single_reauth(session):
    client = _make_client(session)
    client._access_token = "stale"
    with aioresponses() as m:
        m.post(CURRENT_URL, status=401, payload={"msg": "expired"})
        m.post(TOKEN_URL, payload={"success": True, "access_token": "fresh"})
        m.post(
            CURRENT_URL,
            payload={"success": True, "dataList": [{"key": "X", "value": "1"}]},
        )
        result = await client.current_data("SN1")

    assert client.access_token == "fresh"
    assert result["dataList"] == [{"key": "X", "value": "1"}]


@pytest.mark.asyncio
async def test_401_reauth_failure_raises(session):
    client = _make_client(session)
    client._access_token = "stale"
    with aioresponses() as m:
        m.post(CURRENT_URL, status=401, payload={"msg": "expired"})
        m.post(TOKEN_URL, payload={"success": True, "access_token": "fresh"})
        m.post(CURRENT_URL, status=401, payload={"msg": "still bad"})
        with pytest.raises(SolarmanAuthError):
            await client.current_data("SN1")


@pytest.mark.asyncio
async def test_429_respects_retry_after(monkeypatch, session):
    client = _make_client(session)
    client._access_token = "tok"

    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("custom_components.solarman_api.api.asyncio.sleep", _fake_sleep)

    with aioresponses() as m:
        m.post(CURRENT_URL, status=429, headers={"Retry-After": "2"}, body="rl")
        m.post(
            CURRENT_URL,
            payload={"success": True, "dataList": [{"key": "K", "value": "v"}]},
        )
        result = await client.current_data("SN1")

    assert sleeps == [2]
    assert result["dataList"] == [{"key": "K", "value": "v"}]


@pytest.mark.asyncio
async def test_429_double_raises(monkeypatch, session):
    client = _make_client(session)
    client._access_token = "tok"

    async def _fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr("custom_components.solarman_api.api.asyncio.sleep", _fake_sleep)

    with aioresponses() as m:
        m.post(CURRENT_URL, status=429, headers={"Retry-After": "5"}, body="rl")
        m.post(CURRENT_URL, status=429, headers={"Retry-After": "5"}, body="rl")
        with pytest.raises(SolarmanRateLimitError) as exc_info:
            await client.current_data("SN1")
    assert exc_info.value.retry_after == 5


@pytest.mark.asyncio
async def test_historical_posts_expected_payload(session):
    client = _make_client(session)
    client._access_token = "tok"
    historical_url = f"{BASE_URL}/device/v1.0/historical"
    with aioresponses() as m:
        m.post(
            historical_url,
            payload={
                "success": True,
                "paramDataList": [
                    {
                        "collectTime": 1_700_000_000,
                        "dataList": [
                            {"key": "generation", "value": "17.58", "unit": "kWh"}
                        ],
                    }
                ],
            },
        )
        result = await client.historical("SN1", "2026-04-15", "2026-04-22", time_type=2)

        requests = [
            req
            for (method, url), entries in m.requests.items()
            for req in entries
            if method == "POST" and "historical" in str(url)
        ]
    assert len(requests) == 1
    assert requests[0].kwargs["json"] == {
        "deviceSn": "SN1",
        "startTime": "2026-04-15",
        "endTime": "2026-04-22",
        "timeType": 2,
    }
    assert result["paramDataList"][0]["dataList"][0]["key"] == "generation"


@pytest.mark.asyncio
async def test_current_data_success_false_raises_api_error(session):
    """Solarman backend RPC failure shape — `_request` must surface this as
    SolarmanApiError so the coordinator's exception branch preserves the
    previous bucket instead of clobbering it with empty data.
    """
    client = _make_client(session)
    client._access_token = "tok"
    with aioresponses() as m:
        m.post(
            CURRENT_URL,
            payload={
                "success": False,
                "code": 3501004,
                "msg": "remote rpc exception",
                "dataList": None,
            },
        )
        with pytest.raises(SolarmanApiError) as exc_info:
            await client.current_data("SN1")
    assert "3501004" in str(exc_info.value)
    assert "remote rpc exception" in str(exc_info.value)


@pytest.mark.asyncio
async def test_historical_success_false_raises_api_error(session):
    """Same envelope on the historical endpoint must also raise — otherwise
    the statistics import silently skips the day with no warning.
    """
    client = _make_client(session)
    client._access_token = "tok"
    historical_url = f"{BASE_URL}/device/v1.0/historical"
    with aioresponses() as m:
        m.post(
            historical_url,
            payload={
                "success": False,
                "code": 3501004,
                "msg": "remote rpc exception",
                "paramDataList": None,
            },
        )
        with pytest.raises(SolarmanApiError):
            await client.historical("SN1", "2026-04-15", "2026-04-22", time_type=2)


@pytest.mark.asyncio
async def test_list_stations_success_false_raises_api_error(session):
    """Setup-time endpoint: failing should bubble up so `__init__` raises
    ConfigEntryNotReady (HA retries) instead of getting an empty list and
    bailing with a misleading "No stations reported" error.
    """
    client = _make_client(session)
    client._access_token = "tok"
    stations_url = f"{BASE_URL}/station/v1.0/list"
    with aioresponses() as m:
        m.post(
            stations_url,
            payload={
                "success": False,
                "code": 3501004,
                "msg": "remote rpc exception",
                "stationList": None,
            },
        )
        with pytest.raises(SolarmanApiError):
            await client.list_stations()


@pytest.mark.asyncio
async def test_json_parse_error_raises_api_error(session):
    client = _make_client(session)
    client._access_token = "tok"
    with aioresponses() as m:
        m.post(CURRENT_URL, body="<html>nope</html>", status=200)
        with pytest.raises(SolarmanApiError):
            await client.current_data("SN1")


@pytest.mark.asyncio
async def test_concurrent_401s_reauthenticate_once(session):
    """Devices polled together all get 401 when the token expires; one re-auth."""
    import asyncio

    from aioresponses import CallbackResult

    client = _make_client(session)
    client._access_token = "stale"
    token_requests = 0

    def _token(url, **kwargs):
        nonlocal token_requests
        token_requests += 1
        return CallbackResult(
            payload={"success": True, "access_token": f"fresh{token_requests}"}
        )

    stale_seen = 0
    all_rejected = asyncio.Event()

    async def _current(url, **kwargs):
        nonlocal stale_seen
        if kwargs["headers"]["Authorization"] == "bearer stale":
            # Answer only once all three polls are in flight with the old token
            stale_seen += 1
            if stale_seen == 3:
                all_rejected.set()
            await all_rejected.wait()
            return CallbackResult(status=401, payload={"msg": "expired"})
        return CallbackResult(payload={"success": True, "dataList": []})

    with aioresponses() as m:
        m.post(TOKEN_URL, callback=_token, repeat=True)
        m.post(CURRENT_URL, callback=_current, repeat=True)
        results = await asyncio.gather(
            client.current_data("SN1"),
            client.current_data("SN2"),
            client.current_data("SN3"),
        )

    assert token_requests == 1
    assert results == [{"success": True, "dataList": []}] * 3


@pytest.mark.asyncio
async def test_429_with_long_retry_after_raises_without_sleeping(monkeypatch, session):
    """A long Retry-After isn't slept through inside a coordinator update."""
    client = _make_client(session)
    client._access_token = "tok"

    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("custom_components.solarman_api.api.asyncio.sleep", _fake_sleep)

    with aioresponses() as m:
        m.post(CURRENT_URL, status=429, headers={"Retry-After": "3600"}, body="rl")
        with pytest.raises(SolarmanRateLimitError) as exc_info:
            await client.current_data("SN1")

    assert sleeps == []
    assert exc_info.value.retry_after == 3600
