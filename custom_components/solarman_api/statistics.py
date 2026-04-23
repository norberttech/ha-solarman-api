"""Import energy totals from the Solarman historical endpoint into HA long-term statistics.

Two granularities written:

- **Daily rows** for every completed day in the requested window (yesterday
  and earlier). Each row carries that day's total production/consumption.
- **Hourly rows** for the current day from 00:00 UTC up to the last
  completed hour, aggregated from the 5-minute snapshots Solarman returns
  via `time_type=1`. This lets the Energy dashboard show today's bars
  immediately after a fresh install, before the live TOTAL_INCREASING
  compile has had time to run.

All rows land on the live sensor's `sensor.<sn>_<key>_total` statistic_id
so the Energy dashboard picks them up without extra user configuration.
Running sums and states are chained daily → hourly → live compile so the
boundaries produce no spikes.
"""
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
from homeassistant.util import dt as dt_util

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
    """Normalize Solarman's daily `collectTime` (ISO date or epoch) to midnight UTC."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            # Might be an epoch string, fall through to numeric parsing.
            try:
                ts = int(raw)
            except ValueError:
                return None
            return _parse_epoch(ts, snap_to_midnight=True)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    try:
        ts = int(raw)
    except (TypeError, ValueError):
        return None
    return _parse_epoch(ts, snap_to_midnight=True)


def _parse_epoch(ts: int, *, snap_to_midnight: bool) -> datetime | None:
    """Turn a unix epoch (seconds or milliseconds) into a UTC datetime."""
    if ts > 10**11:
        ts //= 1000
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    except (OSError, OverflowError):
        return None
    if snap_to_midnight:
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return dt


def _build_daily_rows(
    days: list[tuple[datetime, float]],
    start_offset: float,
) -> list[StatisticData]:
    """Turn (day, daily_kwh) pairs into HA statistic rows.

    Shape balances two conflicting requirements on the same statistic_id:

    - **Energy dashboard daily bars** come from successive `sum` deltas.
      `sum` has to start at 0 and grow by one daily value per row so bars
      equal the per-day values and no spike lands on the earliest day.
    - **Boundary continuity** with a later hourly segment or the live
      TOTAL_INCREASING compile: the last row's `state` has to equal the
      cumulative sensor reading at the end of the last imported day.

    Resolution: `sum` runs 0..total_daily. `state` runs
    start_offset..start_offset+total_daily. Caller sets `start_offset =
    state_at_end_of_last_day - total_daily` so the last row's state matches
    reality.

    A seed row is prepended at `first_day - 1 day` with `sum=0` and
    `state=start_offset`; its `change` is 0 so it renders nothing on the
    dashboard but establishes the baseline for the first real bar.
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


def _aggregate_samples_to_hourly(
    samples: list[tuple[datetime, float]],
    now_utc: datetime,
) -> list[tuple[datetime, float]]:
    """Bucket 5-minute cumulative snapshots into `(hour_start_utc, end_state)` pairs.

    For each hour from the earliest sample's hour through the hour before
    `now_utc`, pick the last sample inside that hour — that sample's value
    is the cumulative counter at end of that hour. The current (partial)
    hour is left out so the live TOTAL_INCREASING compile can own it.
    """
    if not samples:
        return []
    ordered = sorted(samples, key=lambda p: p[0])
    by_hour: dict[datetime, float] = {}
    for ts, value in ordered:
        hour = ts.replace(minute=0, second=0, microsecond=0)
        # Last sample per hour wins (samples are sorted ascending).
        by_hour[hour] = value
    current_hour = now_utc.replace(minute=0, second=0, microsecond=0)
    return [
        (hour, value) for hour, value in sorted(by_hour.items()) if hour < current_hour
    ]


def _build_today_hourly_rows(
    hourly_end_states: list[tuple[datetime, float]],
    starting_state: float,
    starting_sum: float,
) -> list[StatisticData]:
    """Build hourly rows for today from aggregated end-of-hour cumulative states.

    Picks up where the daily import left off (`starting_state` = last daily
    row's `state`, `starting_sum` = last daily row's `sum`) and emits one
    row per hour. Each hour's `state` is the Solarman snapshot; each hour's
    bar = `state[hour] - state[prev_hour]`, reflecting actual production.
    A reading that drops below the previous hour (shouldn't happen for a
    lifetime counter, but guard against API glitches) contributes 0 to sum
    and doesn't pull the running total down.
    """
    rows: list[StatisticData] = []
    prev_state = starting_state
    running_sum = starting_sum
    for hour_start, end_state in hourly_end_states:
        delta = end_state - prev_state
        if delta < 0:
            # API glitch — counter shouldn't decrease. Keep state flat so the
            # bar is 0 but sum doesn't go backward.
            delta = 0.0
            end_state = prev_state
        running_sum += delta
        rows.append(
            StatisticData(start=hour_start, state=end_state, sum=running_sum)
        )
        prev_state = end_state
    return rows


def _live_sensor_state(
    hass: HomeAssistant, device_sn: str, cd_key: str
) -> tuple[str | None, float | None]:
    """Resolve the live sensor's entity_id and its current numeric state."""
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


async def _fetch_daily_window(
    client: SolarmanClient,
    device_sn: str,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    """Pull daily samples for a range, chunking to Solarman's 31-day cap."""
    entries: list[dict[str, Any]] = []
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
                "Historical daily request failed for %s..%s: %s",
                window_start,
                window_end,
                err,
            )
            window_start = window_end + timedelta(days=1)
            continue
        entries.extend(response.get("paramDataList") or [])
        window_start = window_end + timedelta(days=1)
    return entries


async def _fetch_today_5min(
    client: SolarmanClient,
    device_sn: str,
    today: date,
) -> list[dict[str, Any]]:
    """Pull 5-minute samples for a single day (today)."""
    try:
        response = await client.historical(
            device_sn, today.isoformat(), today.isoformat(), time_type=1
        )
    except SolarmanApiError as err:
        _LOGGER.warning("Today 5-min request failed for %s: %s", today, err)
        return []
    return list(response.get("paramDataList") or [])


async def async_import_historical_statistics(
    hass: HomeAssistant,
    client: SolarmanClient,
    devices: list[dict[str, Any]],
    start_date: date,
    end_date: date,
    target_device_sn: str | None = None,
) -> dict[str, Any]:
    """Backfill HA long-term statistics from the Solarman historical endpoint.

    Daily rows for `start_date..min(end_date, yesterday)`; hourly rows for
    today (from 00:00 UTC to the last completed hour) derived from
    `time_type=1` 5-minute snapshots. The daily import's last-row state is
    anchored to the first 5-minute snapshot of today so the handoff from
    daily → hourly → live compile is smooth (no spike at any boundary).

    Returns a summary dict keyed by channel with `daily` and `hourly` row
    counts, plus `total` across everything.
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

    # Solarman's historical endpoint interprets date parameters in the
    # user's local time (account timezone). Use HA's configured timezone so
    # our "today" matches what the user sees on the dashboard — near the
    # local midnight boundary the UTC day can be one ahead/behind.
    local_tz = dt_util.get_time_zone(hass.config.time_zone) or timezone.utc
    today = datetime.now(tz=local_tz).date()
    daily_end = min(end_date, today - timedelta(days=1))
    include_today = end_date >= today

    daily_entries = (
        await _fetch_daily_window(client, device_sn, start_date, daily_end)
        if start_date <= daily_end
        else []
    )
    today_entries = (
        await _fetch_today_5min(client, device_sn, today) if include_today else []
    )

    daily_by_key: dict[str, list[tuple[datetime, float]]] = {}
    for entry in daily_entries:
        day_start = _parse_collect_time(entry.get("collectTime"))
        if day_start is None:
            continue
        for item in entry.get("dataList") or []:
            key = item.get("key")
            if key not in HISTORICAL_KEY_MAP:
                continue
            try:
                value = float(item.get("value"))
            except (TypeError, ValueError):
                continue
            daily_by_key.setdefault(key, []).append((day_start, value))

    # today's samples keyed by the currentData cd_key (e.g. "Et_ge0"),
    # because 5-minute entries carry the live cumulative counter by that
    # same key — not by the daily-report historical key.
    today_samples_by_cd_key: dict[str, list[tuple[datetime, float]]] = {}
    for entry in today_entries:
        raw_ts = entry.get("collectTime")
        try:
            ts_int = int(raw_ts)
        except (TypeError, ValueError):
            continue
        ts = _parse_epoch(ts_int, snap_to_midnight=False)
        if ts is None:
            continue
        for item in entry.get("dataList") or []:
            cd_key = item.get("key")
            if not isinstance(cd_key, str):
                continue
            try:
                value = float(item.get("value"))
            except (TypeError, ValueError):
                continue
            today_samples_by_cd_key.setdefault(cd_key, []).append((ts, value))

    now_utc = datetime.now(tz=timezone.utc)

    per_channel: dict[str, dict[str, int]] = {}
    for historical_key, (cd_key, display_name) in HISTORICAL_KEY_MAP.items():
        daily_values = daily_by_key.get(historical_key, [])
        today_samples = today_samples_by_cd_key.get(cd_key, [])

        entity_id, live_value = _live_sensor_state(hass, device_sn, cd_key)
        if entity_id is None:
            _LOGGER.warning(
                "Entity for %s not registered yet; skipping channel %s",
                cd_key,
                historical_key,
            )
            per_channel[historical_key] = {"daily": 0, "hourly": 0}
            continue

        hourly_end_states = _aggregate_samples_to_hourly(today_samples, now_utc)

        # Anchor alignment: last daily row's state = end-of-last-day state ≈
        # first 5-minute snapshot of today. Falling back to live_value is
        # less accurate but keeps the chain intact when today's samples are
        # unavailable (early install, Solarman lag, etc.).
        if today_samples:
            end_of_yesterday_state = sorted(today_samples, key=lambda p: p[0])[0][1]
        elif live_value is not None:
            end_of_yesterday_state = live_value
        else:
            end_of_yesterday_state = 0.0
            _LOGGER.warning(
                "No 5-min samples and no live state for %s; importing without "
                "alignment (first bar may be oversized)",
                entity_id,
            )

        total_daily = sum(v for _, v in daily_values)
        start_offset = end_of_yesterday_state - total_daily

        daily_rows = _build_daily_rows(daily_values, start_offset)

        # Daily rows' end state == end_of_yesterday_state by construction;
        # hourly rows continue from there.
        last_daily_state = daily_rows[-1]["state"] if daily_rows else start_offset
        last_daily_sum = daily_rows[-1]["sum"] if daily_rows else 0.0

        hourly_rows = _build_today_hourly_rows(
            hourly_end_states,
            starting_state=last_daily_state,
            starting_sum=last_daily_sum,
        )

        all_rows = daily_rows + hourly_rows
        if not all_rows:
            per_channel[historical_key] = {"daily": 0, "hourly": 0}
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
        async_import_statistics(hass, metadata, all_rows)
        per_channel[historical_key] = {
            "daily": len(daily_rows),
            "hourly": len(hourly_rows),
        }
        _LOGGER.debug(
            "Imported %d daily + %d hourly for %s (%s)",
            len(daily_rows),
            len(hourly_rows),
            entity_id,
            historical_key,
        )

    total = sum(c["daily"] + c["hourly"] for c in per_channel.values())
    _LOGGER.info(
        "Historical import complete: %d rows across %d channels (%s..%s, today=%s)",
        total,
        sum(1 for c in per_channel.values() if c["daily"] or c["hourly"]),
        start_date,
        end_date,
        include_today,
    )
    return {
        "total": total,
        "channels": per_channel,
        "device_sn": device_sn,
        "today_hours_imported": include_today and bool(today_entries),
    }
