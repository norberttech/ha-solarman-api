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


def test_build_stat_rows_fresh_install_starts_from_zero() -> None:
    days = [
        (_utc(2026, 4, 1), 10.0),
        (_utc(2026, 4, 2), 12.5),
        (_utc(2026, 4, 3), 8.0),
    ]
    rows = _build_stat_rows(days, last_sum=0.0, last_start=None)
    assert [r["sum"] for r in rows] == [10.0, 22.5, 30.5]
    assert [r["state"] for r in rows] == [10.0, 12.5, 8.0]
    assert [r["start"] for r in rows] == [d for d, _ in days]


def test_build_stat_rows_sorts_by_day() -> None:
    days = [
        (_utc(2026, 4, 3), 8.0),
        (_utc(2026, 4, 1), 10.0),
        (_utc(2026, 4, 2), 12.5),
    ]
    rows = _build_stat_rows(days, last_sum=0.0, last_start=None)
    assert [r["start"] for r in rows] == [
        _utc(2026, 4, 1),
        _utc(2026, 4, 2),
        _utc(2026, 4, 3),
    ]


def test_build_stat_rows_skips_already_imported_days() -> None:
    days = [
        (_utc(2026, 4, 1), 10.0),
        (_utc(2026, 4, 2), 12.5),
        (_utc(2026, 4, 3), 8.0),
    ]
    rows = _build_stat_rows(
        days, last_sum=100.0, last_start=_utc(2026, 4, 2)
    )
    # Only 4/3 should be imported; running sum continues from 100.
    assert len(rows) == 1
    assert rows[0]["start"] == _utc(2026, 4, 3)
    assert rows[0]["state"] == 8.0
    assert rows[0]["sum"] == 108.0


def test_build_stat_rows_returns_empty_when_everything_already_imported() -> None:
    days = [(_utc(2026, 4, 1), 10.0), (_utc(2026, 4, 2), 12.5)]
    rows = _build_stat_rows(days, last_sum=50.0, last_start=_utc(2026, 4, 2))
    assert rows == []


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
