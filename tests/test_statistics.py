"""Unit tests for statistics backfill helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from pytest import approx as pytest_approx

from custom_components.solarman_api.statistics import (
    HISTORICAL_KEY_MAP,
    MAX_WINDOW_DAYS,
    _aggregate_samples_to_hourly,
    _build_daily_rows,
    _build_today_hourly_rows,
    _parse_collect_time,
    _parse_epoch,
)


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def test_key_map_covers_energy_dashboard_channels() -> None:
    # Sanity: every historical key maps to a currentData cumulative-total key.
    assert set(HISTORICAL_KEY_MAP) == {
        "generation",
        "grid",
        "purchase",
        "consumption",
        "charge",
        "discharge",
    }
    for _, (cd_key, name) in HISTORICAL_KEY_MAP.items():
        assert cd_key.endswith(("_ge0", "_gc1", "_pu1", "_use1", "_cg_n1", "_dcg_n1"))
        assert name


def test_build_daily_rows_no_offset_starts_from_zero() -> None:
    days = [
        (_utc(2026, 4, 1), 10.0),
        (_utc(2026, 4, 2), 12.5),
        (_utc(2026, 4, 3), 8.0),
    ]
    rows = _build_daily_rows(days, start_offset=0.0)
    # With no offset, sum and state both run 0..total. Energy dashboard
    # bars come from successive sum-deltas and equal the per-day values.
    assert [r["sum"] for r in rows] == [0.0, 10.0, 22.5, 30.5]
    assert [r["state"] for r in rows] == [0.0, 10.0, 22.5, 30.5]
    assert [r["start"] for r in rows] == [_utc(2026, 3, 31)] + [d for d, _ in days]


def test_build_daily_rows_seed_has_zero_sum_prevents_spike() -> None:
    """`sum` must start at 0 at the seed row so the first real day's bar is
    just its daily value. If the seed carried the offset in `sum`, HA would
    render that offset as a bar on the seed day (tens of thousands of kWh).
    """
    days = [
        (_utc(2026, 4, 20), 13.87),
        (_utc(2026, 4, 21), 30.92),
    ]
    rows = _build_daily_rows(days, start_offset=10000.0)
    # Seed row at Apr 19: sum=0 (not a bar), state=offset (for boundary)
    assert rows[0]["start"] == _utc(2026, 4, 19)
    assert rows[0]["sum"] == 0.0
    assert rows[0]["state"] == 10000.0
    # Apr 20 bar = sum[Apr 20] - sum[Apr 19] = 13.87 - 0 = 13.87
    # Apr 21 bar = sum[Apr 21] - sum[Apr 20] = 30.92
    assert rows[1]["sum"] - rows[0]["sum"] == pytest_approx(13.87)
    assert rows[2]["sum"] - rows[1]["sum"] == pytest_approx(30.92)
    # `state` ends at offset + total so the next segment (hourly or live
    # compile) sees a ~zero delta against the inverter's current reading.
    assert rows[-1]["state"] == pytest_approx(10000.0 + 13.87 + 30.92)


def test_build_daily_rows_sorts_by_day() -> None:
    days = [
        (_utc(2026, 4, 3), 8.0),
        (_utc(2026, 4, 1), 10.0),
        (_utc(2026, 4, 2), 12.5),
    ]
    rows = _build_daily_rows(days, start_offset=0.0)
    # Seed row at day-1 (Mar 31) plus three sorted import days.
    assert [r["start"] for r in rows] == [
        _utc(2026, 3, 31),
        _utc(2026, 4, 1),
        _utc(2026, 4, 2),
        _utc(2026, 4, 3),
    ]


def test_build_daily_rows_empty_input_returns_empty() -> None:
    assert _build_daily_rows([], start_offset=100.0) == []


def test_aggregate_samples_to_hourly_last_sample_per_hour_wins() -> None:
    """Each hour keeps the latest 5-min snapshot as its end-of-hour state."""
    samples = [
        (_utc(2026, 4, 23, 0, 5), 100.0),
        (_utc(2026, 4, 23, 0, 35), 101.5),
        (_utc(2026, 4, 23, 0, 55), 102.0),  # last in hour 0 -> end state
        (_utc(2026, 4, 23, 1, 10), 103.0),
        (_utc(2026, 4, 23, 1, 50), 105.5),  # last in hour 1
        (_utc(2026, 4, 23, 2, 5), 106.0),  # current partial hour (will be skipped)
    ]
    hourly = _aggregate_samples_to_hourly(samples, now_utc=_utc(2026, 4, 23, 2, 15))
    assert hourly == [
        (_utc(2026, 4, 23, 0), 102.0),
        (_utc(2026, 4, 23, 1), 105.5),
    ]


def test_aggregate_samples_to_hourly_drops_current_partial_hour() -> None:
    """The current (partial) hour is left to the live TOTAL_INCREASING compile."""
    samples = [(_utc(2026, 4, 23, 5, 30), 200.0)]
    hourly = _aggregate_samples_to_hourly(samples, now_utc=_utc(2026, 4, 23, 5, 45))
    assert hourly == []


def test_build_today_hourly_rows_continues_sum_and_state() -> None:
    hourly_end_states = [
        (_utc(2026, 4, 23, 0), 43977.7),
        (_utc(2026, 4, 23, 1), 43977.7),
        (_utc(2026, 4, 23, 2), 43978.2),
        (_utc(2026, 4, 23, 3), 43981.0),
    ]
    rows = _build_today_hourly_rows(
        hourly_end_states,
        starting_state=43977.7,
        starting_sum=3196.17,
    )
    assert [r["state"] for r in rows] == pytest_approx(
        [43977.7, 43977.7, 43978.2, 43981.0]
    )
    # hour 0: 43977.7-43977.7 = 0 gain
    # hour 1: 0 gain
    # hour 2: +0.5
    # hour 3: +2.8
    assert [round(r["sum"] - 3196.17, 2) for r in rows] == [0.0, 0.0, 0.5, 3.3]


def test_build_today_hourly_rows_clamps_counter_decreases() -> None:
    """A counter decrease (API glitch) contributes 0 to sum and leaves state flat
    — don't let a one-off dip pull the running total backwards.
    """
    hourly_end_states = [
        (_utc(2026, 4, 23, 0), 100.0),
        (_utc(2026, 4, 23, 1), 95.0),  # dip — should be clamped
        (_utc(2026, 4, 23, 2), 102.0),  # recovery — delta vs the clamped 100.0
    ]
    rows = _build_today_hourly_rows(
        hourly_end_states, starting_state=100.0, starting_sum=0.0
    )
    assert [r["state"] for r in rows] == [100.0, 100.0, 102.0]
    assert [r["sum"] for r in rows] == [0.0, 0.0, 2.0]


def test_build_today_hourly_rows_empty_returns_empty() -> None:
    assert _build_today_hourly_rows([], starting_state=100.0, starting_sum=50.0) == []


def test_parse_collect_time_iso_string() -> None:
    # Live API returns e.g. "2026-04-15" for daily historical samples.
    assert _parse_collect_time("2026-04-15") == _utc(2026, 4, 15)


def test_parse_collect_time_unix_seconds() -> None:
    # Apr 15 2026 00:00 UTC
    assert _parse_collect_time(1776211200) == _utc(2026, 4, 15)


def test_parse_collect_time_unix_milliseconds() -> None:
    assert _parse_collect_time(1776211200000) == _utc(2026, 4, 15)


def test_parse_collect_time_invalid_returns_none() -> None:
    assert _parse_collect_time(None) is None
    assert _parse_collect_time("not-a-date") is None
    assert _parse_collect_time("") is None


def test_parse_epoch_preserves_hour_minute_when_not_snapping() -> None:
    # The 5-minute endpoint returns entries with collectTime at the actual
    # sample moment (unix seconds in UTC). For today's hourly import we
    # need to keep those sub-hour timestamps so aggregation buckets
    # correctly into UTC hour windows.
    # 1776895383 = 2026-04-22 22:03:03 UTC (local Warsaw: 2026-04-23 00:03).
    dt = _parse_epoch(1776895383, snap_to_midnight=False)
    assert dt is not None
    assert dt == datetime(2026, 4, 22, 22, 3, 3, tzinfo=timezone.utc)


def test_max_window_days_respects_api_cap() -> None:
    # Solarman returns code 2101012 "should be within 31 days" for wider
    # historical queries with timeType=2. Keep this <= 31 so each chunk is
    # guaranteed to hit the capped ceiling rather than silently return nothing.
    assert 1 <= MAX_WINDOW_DAYS <= 31
