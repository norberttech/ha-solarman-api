"""Unit tests for statistics backfill helpers."""
from __future__ import annotations

from datetime import datetime, timezone

from custom_components.solarman_api.statistics import (
    HISTORICAL_KEY_MAP,
    MAX_WINDOW_DAYS,
    _build_stat_rows,
    _parse_collect_time,
)


def _utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


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


def test_build_stat_rows_no_offset_starts_from_zero() -> None:
    days = [
        (_utc(2026, 4, 1), 10.0),
        (_utc(2026, 4, 2), 12.5),
        (_utc(2026, 4, 3), 8.0),
    ]
    rows = _build_stat_rows(days, start_offset=0.0)
    # state and sum both carry the cumulative running total so the recorder
    # sees a monotonically increasing energy series; daily deltas between
    # successive sums equal the per-day values the Energy dashboard plots.
    assert [r["sum"] for r in rows] == [10.0, 22.5, 30.5]
    assert [r["state"] for r in rows] == [10.0, 22.5, 30.5]
    assert [r["start"] for r in rows] == [d for d, _ in days]


def test_build_stat_rows_sorts_by_day() -> None:
    days = [
        (_utc(2026, 4, 3), 8.0),
        (_utc(2026, 4, 1), 10.0),
        (_utc(2026, 4, 2), 12.5),
    ]
    rows = _build_stat_rows(days, start_offset=0.0)
    assert [r["start"] for r in rows] == [
        _utc(2026, 4, 1),
        _utc(2026, 4, 2),
        _utc(2026, 4, 3),
    ]


def test_build_stat_rows_alignment_ends_at_live_counter() -> None:
    """Caller computes `start_offset = live_lifetime - total_import` so the
    final imported row's `state` matches what the live TOTAL_INCREASING
    sensor currently reports. Without this, the next live compile sees a
    delta of tens of thousands of kWh and dumps it into one hourly bar.
    """
    days = [
        (_utc(2026, 4, 20), 13.87),
        (_utc(2026, 4, 21), 30.92),
        (_utc(2026, 4, 22), 15.21),
    ]
    total = sum(v for _, v in days)
    live_lifetime = 43977.7
    rows = _build_stat_rows(days, start_offset=live_lifetime - total)
    # Last row ends exactly at the live counter.
    assert rows[-1]["state"] == live_lifetime
    assert rows[-1]["sum"] == live_lifetime
    # Per-day deltas are preserved regardless of offset.
    deltas = [rows[0]["sum"] - (live_lifetime - total)] + [
        rows[i]["sum"] - rows[i - 1]["sum"] for i in range(1, len(rows))
    ]
    assert [round(d, 2) for d in deltas] == [13.87, 30.92, 15.21]


def test_build_stat_rows_empty_input_returns_empty() -> None:
    assert _build_stat_rows([], start_offset=100.0) == []


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


def test_max_window_days_respects_api_cap() -> None:
    # Solarman returns code 2101012 "should be within 31 days" for wider
    # historical queries with timeType=2. Keep this <= 31 so each chunk is
    # guaranteed to hit the capped ceiling rather than silently return nothing.
    assert 1 <= MAX_WINDOW_DAYS <= 31
