"""Probe: poll currentData every 60s for both devices and log response shape.

Helps diagnose why inverter sensors occasionally flip to `unknown`. Writes one
line per (time, device) to stdout. Run from the repo root with:

    .venv/bin/python tests/probe_current_data.py 25    # run for 25 minutes
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _load_env_file() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


from custom_components.solarman_api.api import (  # noqa: E402
    SolarmanApiError,
    SolarmanClient,
)


async def _probe_once(client: SolarmanClient, sn: str) -> str:
    wall = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        resp = await client.current_data(sn)
    except SolarmanApiError as err:
        return f"{wall} sn={sn} ERROR {err.status}: {str(err)[:160]}"
    except Exception as err:  # noqa: BLE001
        return f"{wall} sn={sn} EXC {type(err).__name__}: {err}"

    data_list = resp.get("dataList") or []
    keys = {item.get("key") for item in data_list if isinstance(item, dict)}
    ct = resp.get("collectionTime")
    if isinstance(ct, (int, float)):
        ct_iso = datetime.fromtimestamp(int(ct), tz=timezone.utc).isoformat(
            timespec="seconds"
        )
        age_s = int(time.time() - int(ct))
    else:
        ct_iso = str(ct)
        age_s = -1
    # Flag status keys specifically (those are the ones going unknown).
    has_inv_st = "INV_ST1" in keys
    has_pg_st = "ST_PG1" in keys
    has_ap1 = "AP1" in keys  # AC power — should always be present
    status_marker = "OK"
    if len(data_list) == 0:
        status_marker = "EMPTY_DATALIST"
    elif not (has_inv_st and has_pg_st and has_ap1):
        status_marker = f"MISSING(inv={has_inv_st},pg={has_pg_st},ap1={has_ap1})"
    return (
        f"{wall} sn={sn} {status_marker} "
        f"keys={len(keys)} collectionTime={ct_iso} age={age_s}s "
        f"code={resp.get('code')} success={resp.get('success')} msg={resp.get('msg')!r}"
    )


async def main(minutes: int) -> None:
    _load_env_file()
    for var in (
        "SOLARMAN_EMAIL",
        "SOLARMAN_PASSWORD",
        "SOLARMAN_APP_ID",
        "SOLARMAN_APP_SECRET",
    ):
        if not os.environ.get(var):
            raise SystemExit(f"missing {var}")

    deadline = time.time() + minutes * 60
    async with aiohttp.ClientSession(trust_env=True) as session:
        client = SolarmanClient(
            session=session,
            app_id=os.environ["SOLARMAN_APP_ID"],
            app_secret=os.environ["SOLARMAN_APP_SECRET"],
            email=os.environ["SOLARMAN_EMAIL"],
            password=os.environ["SOLARMAN_PASSWORD"],
        )
        await client.authenticate()
        stations = await client.list_stations()
        station_id = int(stations[0]["id"])
        devices = await client.list_devices(station_id)
        sns = [str(d["deviceSn"]) for d in devices]
        print(f"# devices: {sns}", flush=True)

        while time.time() < deadline:
            tasks = [_probe_once(client, sn) for sn in sns]
            for line in await asyncio.gather(*tasks):
                print(line, flush=True)
            # Poll every 60s so we line up with HA's 5-min poll ~5 times per window.
            await asyncio.sleep(60)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    asyncio.run(main(n))
