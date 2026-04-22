"""Import daily kWh totals from the Solarman historical endpoint into HA long-term statistics."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant

from .api import SolarmanApiError, SolarmanClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Solarman's /device/v1.0/historical endpoint rejects date ranges wider than
# 31 days with code 2101012 ("should be within 31 days"). We chunk the full
# window the caller requested into sub-windows of this size.
MAX_WINDOW_DAYS = 30

# Historical daily key -> (currentData cumulative-total key, human name).
HISTORICAL_KEY_MAP: dict[str, tuple[str, str]] = {
    "generation": ("Et_ge0", "Solar production"),
    "grid": ("t_gc1", "Grid export"),
    "purchase": ("Et_pu1", "Grid import"),
    "consumption": ("Et_use1", "Home consumption"),
    "charge": ("t_cg_n1", "Battery charge"),
    "discharge": ("t_dcg_n1", "Battery discharge"),
}


def historical_statistic_id(device_sn: str, cd_key: str) -> str:
    """Build the external statistic_id for an imported historical channel.

    Format is `{domain}:{object_id}` — required by HA for external
    statistics. Keeping it separate from the live sensor's entity_id
    (sensor.solarman_inverter_*) prevents the recorder from blending
    imported daily deltas with the live TOTAL_INCREASING compile, which
    used to produce spurious negative bars at the boundary.
    """
    object_id = f"{device_sn}_{cd_key}_historical".lower().replace("-", "_")
    return f"{DOMAIN}:{object_id}"


def _parse_collect_time(raw: Any) -> datetime | None:
    """Normalize Solarman's `collectTime` (string date or epoch) to midnight UTC.

    Historical endpoint returns `collectTime` as an ISO date string like
    "2026-04-15" for daily samples; other endpoints use unix seconds or
    milliseconds. Accept all three forms; return None on failure.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    try:
        ts = int(raw)
    except (TypeError, ValueError):
        return None
    if ts > 10**11:  # milliseconds
        ts //= 1000
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    except (OSError, OverflowError):
        return None


def _build_stat_rows(
    days: list[tuple[datetime, float]],
    last_sum: float,
    last_start: datetime | None,
) -> list[StatisticData]:
    """Turn (day, daily_kwh) pairs into HA statistic rows with a running sum.

    `state` is set to the running cumulative total at end of each day — the
    shape HA's recorder expects for an energy statistic whose `sum` grows
    monotonically. Writing the daily delta into `state` (as this function
    used to do) creates a boundary discontinuity when a live TOTAL_INCREASING
    sensor shares the statistic_id.

    Skips days at or before `last_start` so re-running is idempotent.
    Pure function: no HA, no I/O — easy to unit-test.
    """
    rows: list[StatisticData] = []
    running_sum = last_sum
    for day_start, daily_value in sorted(days, key=lambda p: p[0]):
        if last_start is not None and day_start <= last_start:
            continue
        running_sum += daily_value
        rows.append(
            StatisticData(start=day_start, state=running_sum, sum=running_sum)
        )
    return rows


async def async_import_historical_statistics(
    hass: HomeAssistant,
    client: SolarmanClient,
    devices: list[dict[str, Any]],
    start_date: date,
    end_date: date,
    target_device_sn: str | None = None,
) -> dict[str, Any]:
    """Fetch daily energy totals for the given date range and import them.

    Writes each channel to its own external statistic_id
    (`solarman_api:<deviceSn>_<cdKey>_historical`). Users can then add those
    statistics as sources in the HA Energy dashboard if they want historical
    backfill alongside the live sensor. Imported data will not collide with
    the live sensor's own TOTAL_INCREASING stats compile.

    Returns a summary dict `{channel: imported_rows}` plus `total`. Raises
    nothing; errors per window are logged and the function continues.
    """
    if target_device_sn:
        inverter = next(
            (d for d in devices if d.get("deviceSn") == target_device_sn), None
        )
    else:
        inverter = next(
            (d for d in devices if d.get("deviceType") == "INVERTER"), None
        )
    if inverter is None:
        return {"total": 0, "error": "no inverter device found"}

    device_sn = inverter["deviceSn"]
    if end_date < start_date:
        return {"total": 0, "error": "end before start"}

    # Chunk into <=MAX_WINDOW_DAYS sub-ranges; Solarman rejects wider queries.
    param_data_list: list[dict[str, Any]] = []
    window_start = start_date
    while window_start <= end_date:
        window_end = min(window_start + timedelta(days=MAX_WINDOW_DAYS - 1), end_date)
        try:
            response = await client.historical(
                device_sn,
                window_start.isoformat(),
                window_end.isoformat(),
                time_type=2,
            )
        except SolarmanApiError as err:
            _LOGGER.warning(
                "Historical request failed for %s..%s: %s",
                window_start,
                window_end,
                err,
            )
            window_start = window_end + timedelta(days=1)
            continue
        window_data = response.get("paramDataList") or []
        _LOGGER.debug(
            "Historical window %s..%s -> %d entries (success=%s)",
            window_start,
            window_end,
            len(window_data),
            response.get("success"),
        )
        param_data_list.extend(window_data)
        window_start = window_end + timedelta(days=1)

    if not param_data_list:
        return {"total": 0, "error": "no data returned"}

    daily_by_key: dict[str, list[tuple[datetime, float]]] = {}
    for entry in param_data_list:
        day_start = _parse_collect_time(entry.get("collectTime"))
        if day_start is None:
            continue
        for item in entry.get("dataList", []) or []:
            key = item.get("key")
            if key not in HISTORICAL_KEY_MAP:
                continue
            try:
                value = float(item.get("value"))
            except (TypeError, ValueError):
                continue
            daily_by_key.setdefault(key, []).append((day_start, value))

    recorder = get_instance(hass)
    per_channel: dict[str, int] = {}

    for historical_key, (cd_key, display_name) in HISTORICAL_KEY_MAP.items():
        days = daily_by_key.get(historical_key)
        if not days:
            per_channel[historical_key] = 0
            continue

        statistic_id = historical_statistic_id(device_sn, cd_key)

        last_stats = await recorder.async_add_executor_job(
            get_last_statistics, hass, 1, statistic_id, True, {"sum"}
        )
        last_sum = 0.0
        last_start: datetime | None = None
        rows = last_stats.get(statistic_id) or []
        if rows:
            row = rows[0]
            last_sum = float(row.get("sum") or 0.0)
            last_end = row.get("end")
            if isinstance(last_end, (int, float)):
                last_start = datetime.fromtimestamp(last_end, tz=timezone.utc)

        new_rows = _build_stat_rows(days, last_sum, last_start)
        if not new_rows:
            per_channel[historical_key] = 0
            continue

        metadata = StatisticMetaData(
            has_mean=False,
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"{display_name} (historical)",
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            unit_class="energy",
        )
        async_add_external_statistics(hass, metadata, new_rows)
        per_channel[historical_key] = len(new_rows)
        _LOGGER.debug(
            "Imported %d days for %s (%s)",
            len(new_rows),
            statistic_id,
            historical_key,
        )

    total = sum(per_channel.values())
    _LOGGER.info(
        "Historical import complete: %d rows across %d channels (%s..%s)",
        total,
        sum(1 for n in per_channel.values() if n),
        start_date,
        end_date,
    )
    return {"total": total, "channels": per_channel, "device_sn": device_sn}
