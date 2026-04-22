"""End-to-end smoke test against the real Solarman Open API.

Skipped unless all four SOLARMAN_* env vars are set. This test performs real
network calls against https://globalapi.solarmanpv.com. Never reads api.md.

Run with:

    SOLARMAN_EMAIL=... SOLARMAN_PASSWORD=... \
    SOLARMAN_APP_ID=... SOLARMAN_APP_SECRET=... \
    .venv/bin/pytest tests/test_live.py -s
"""

from __future__ import annotations

import os

import socket

import aiohttp
import pytest
import pytest_socket

from custom_components.solarman_api.api import SolarmanClient
from custom_components.solarman_api.const import BATTERY_SENSORS, INVERTER_SENSORS

_REQUIRED = (
    "SOLARMAN_EMAIL",
    "SOLARMAN_PASSWORD",
    "SOLARMAN_APP_ID",
    "SOLARMAN_APP_SECRET",
)

pytestmark = pytest.mark.skipif(
    not all(os.environ.get(name) for name in _REQUIRED),
    reason=f"requires env vars: {', '.join(_REQUIRED)}",
)


async def test_live_flow() -> None:
    pytest_socket.enable_socket()
    socket.socket.connect = pytest_socket._true_connect
    # trust_env=True so the session honors HTTPS_PROXY when running inside
    # Claude Code's Bash sandbox (or any other proxy-mediated environment).
    async with aiohttp.ClientSession(trust_env=True) as session:
        client = SolarmanClient(
            session=session,
            app_id=os.environ["SOLARMAN_APP_ID"],
            app_secret=os.environ["SOLARMAN_APP_SECRET"],
            email=os.environ["SOLARMAN_EMAIL"],
            password=os.environ["SOLARMAN_PASSWORD"],
        )
        token = await client.authenticate()
        assert token
        print(f"[live] authenticated (token length={len(token)})")

        stations = await client.list_stations()
        assert stations, "no stations returned"
        print(f"[live] stations: {len(stations)}")
        station_id = int(stations[0]["id"])

        devices = await client.list_devices(station_id)
        assert devices, "no devices returned"
        print(f"[live] devices at station {station_id}: {len(devices)}")

        expected_keys = {
            "INVERTER": {d.key for d in INVERTER_SENSORS},
            "BATTERY": {d.key for d in BATTERY_SENSORS},
        }
        device_types_seen: set[str] = set()
        missing_by_device: dict[str, set[str]] = {}

        for device in devices:
            sn = device["deviceSn"]
            dtype = device.get("deviceType", "?")
            data = await client.current_data(sn)
            assert isinstance(data, list)
            returned_keys = {item["key"] for item in data if "key" in item}
            device_types_seen.add(dtype)

            expected = expected_keys.get(dtype, set())
            missing = expected - returned_keys
            extras = returned_keys - expected if expected else set()

            summary = f"{len(data)} keys"
            if expected:
                summary += f", {len(expected)} declared, {len(missing)} missing, {len(extras)} unmapped"
            print(f"[live]   {dtype:<9} {sn}: {summary}")
            if extras:
                sample = sorted(extras)[:10]
                suffix = "" if len(extras) <= 10 else f" (+{len(extras) - 10} more)"
                print(f"[live]     unmapped API keys (first 10): {sample}{suffix}")
            if missing:
                missing_by_device[f"{dtype} {sn}"] = missing

        assert "INVERTER" in device_types_seen, "no INVERTER device on the account"
        assert "BATTERY" in device_types_seen, "no BATTERY device on the account"

        if missing_by_device:
            details = "; ".join(
                f"{label}: {sorted(keys)}" for label, keys in missing_by_device.items()
            )
            raise AssertionError(
                f"Declared sensor keys missing from live API response: {details}"
            )

        # Probe historical endpoint shape against the inverter (last 7 days,
        # daily granularity). We don't assert a specific field shape yet --
        # we're inspecting what the server returns so we can wire up stats.
        import datetime as _dt

        inverter_sn = next(
            d["deviceSn"] for d in devices if d.get("deviceType") == "INVERTER"
        )
        today = _dt.date.today()
        start = today - _dt.timedelta(days=7)
        hist = await client.historical(
            inverter_sn, start.isoformat(), today.isoformat(), time_type=2
        )
        top_keys = sorted(hist.keys())
        print(f"[live] historical top-level keys: {top_keys}")
        for candidate in ("paramDataList", "dataList", "daysList", "list"):
            if candidate in hist and isinstance(hist[candidate], list):
                bucket = hist[candidate]
                print(
                    f"[live]   historical[{candidate!r}]: {len(bucket)} entries"
                )
                if bucket:
                    first = bucket[0]
                    if isinstance(first, dict):
                        print(f"[live]     first entry keys: {sorted(first.keys())}")
                        ct = first.get("collectTime")
                        print(f"[live]     first entry collectTime: {ct!r} (type={type(ct).__name__})")
                        dl = first.get("dataList")
                        if isinstance(dl, list) and dl:
                            all_entries = [
                                (it.get("key"), it.get("value"), it.get("unit"))
                                for it in dl
                            ]
                            print(f"[live]     first entry dataList ({len(dl)} items):")
                            for k, v, u in all_entries:
                                print(f"[live]       - {k}={v} {u or ''}".rstrip())
                break
