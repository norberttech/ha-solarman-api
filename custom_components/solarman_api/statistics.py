"""Import daily kWh totals from the Solarman historical endpoint into HA long-term statistics."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .api import SolarmanApiError, SolarmanClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Solarman's /device/v1.0/historical endpoint rejects date ranges wider than
# 31 days with code 2101012 ("should be within 31 days"). We chunk the full
# window the caller requested into sub-windows of this size.
MAX_WINDOW_DAYS = 30

# Historical daily key -> (currentData cumulative-total key, human name).
HISTORICAL_KEY_MAP: dict[str, tuple[str, str]] = {
    "generation": ("Et_ge0", "Solar production total"),
    "grid": ("t_gc1", "Grid export total"),
    "purchase": ("Et_pu1", "Grid import total"),
    "consumption": ("Et_use1", "Home consumption total"),
    "charge": ("t_cg_n1", "Battery charge total"),
    "discharge": ("t_dcg_n1", "Battery discharge total"),
}


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
    start_offset: float,
) -> list[StatisticData]:
    """Turn (day, daily_kwh) pairs into HA statistic rows.

    The shape balances two conflicting requirements the Energy dashboard
    and the live TOTAL_INCREASING compile put on the same statistic_id:

    - **Energy dashboard daily bars** come from successive `sum` deltas.
      For bars to equal the per-day values (and no spike on the earliest
      day), `sum` has to start at 0 and grow by one daily value per row.

    - **Live boundary compile** compares the newest sensor reading against
      the last row's `state`. For the delta to be ~zero on the first hourly
      compile after the import (no multi-thousand-kWh spike on today's first
      bar), the last row's `state` has to be ≈ the inverter's current
      lifetime counter.

    Resolution: `sum` runs 0..total_of_days (clean bars). `state` runs
    start_offset..start_offset+total_of_days (ending at live_lifetime when
    caller sets `start_offset = live_lifetime - total`). `state` and `sum`
    diverge — that's fine, HA stores them as independent columns and the
    dashboard/boundary each read the column they care about.

    A seed row is prepended at `first_day - 1 day` with `sum=0` and
    `state=start_offset`. The seed's `change` is 0 so it renders nothing on
    the dashboard; it just establishes the baseline for the first real day.

    Pure function: no HA, no I/O — easy to unit-test.
    """
    if not days:
        return []
    ordered = sorted(days, key=lambda p: p[0])
    first_day = ordered[0][0]
    rows: list[StatisticData] = [
        StatisticData(
            start=first_day - timedelta(days=1),
            state=start_offset,
            sum=0.0,
        )
    ]
    state_cursor = start_offset
    running_sum = 0.0
    for day_start, daily_value in ordered:
        state_cursor += daily_value
        running_sum += daily_value
        rows.append(
            StatisticData(start=day_start, state=state_cursor, sum=running_sum)
        )
    return rows


def _live_sensor_state(
    hass: HomeAssistant, device_sn: str, cd_key: str
) -> tuple[str | None, float | None]:
    """Resolve the live sensor's entity_id and current numeric state.

    Returns `(entity_id, state_value)`. `entity_id` is None if the sensor
    hasn't been registered yet. `state_value` is None if the state is
    unknown/unavailable or not numeric — alignment falls back to zero in
    that case and the caller logs a warning.
    """
    ent_reg = er.async_get(hass)
    unique_id = f"{device_sn}_{cd_key}"
    entity_id = ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id)
    if entity_id is None:
        return None, None
    state_obj = hass.states.get(entity_id)
    if state_obj is None or state_obj.state in ("unknown", "unavailable", None, ""):
        return entity_id, None
    try:
        return entity_id, float(state_obj.state)
    except (TypeError, ValueError):
        return entity_id, None


async def async_import_historical_statistics(
    hass: HomeAssistant,
    client: SolarmanClient,
    devices: list[dict[str, Any]],
    start_date: date,
    end_date: date,
    target_device_sn: str | None = None,
) -> dict[str, Any]:
    """Fetch daily energy totals for the given date range and import them.

    The imported rows land on the live sensor's statistic_id so the Energy
    dashboard — which is wired to `sensor.solarman_inverter_*` entities —
    picks up the backfill without any per-user configuration. Each channel's
    running sum is aligned to the live sensor's current lifetime reading so
    the next TOTAL_INCREASING compile after the import sees a sane delta
    instead of a spike.

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

    per_channel: dict[str, int] = {}

    for historical_key, (cd_key, display_name) in HISTORICAL_KEY_MAP.items():
        days = daily_by_key.get(historical_key)
        if not days:
            per_channel[historical_key] = 0
            continue

        entity_id, live_value = _live_sensor_state(hass, device_sn, cd_key)
        if entity_id is None:
            _LOGGER.warning(
                "Entity for %s not registered yet; skipping channel %s",
                cd_key,
                historical_key,
            )
            per_channel[historical_key] = 0
            continue

        total_import = sum(v for _, v in days)
        if live_value is None:
            _LOGGER.warning(
                "Live sensor %s has no numeric state; importing without "
                "alignment (first bar may be oversized)",
                entity_id,
            )
            start_offset = 0.0
        else:
            # Align so the last imported row's cumulative sum equals the
            # inverter's current lifetime reading. The live TOTAL_INCREASING
            # compile will then see last_state ≈ current sensor state and
            # compute a normal hourly delta instead of a massive spike.
            start_offset = live_value - total_import

        new_rows = _build_stat_rows(days, start_offset)
        if not new_rows:
            per_channel[historical_key] = 0
            continue

        metadata = StatisticMetaData(
            has_mean=False,
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=display_name,
            source="recorder",
            statistic_id=entity_id,
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            unit_class="energy",
        )
        async_import_statistics(hass, metadata, new_rows)
        per_channel[historical_key] = len(new_rows)
        _LOGGER.debug(
            "Imported %d days for %s (%s)",
            len(new_rows),
            entity_id,
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
