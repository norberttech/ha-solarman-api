"""Unit tests for derived sensor computations.

Sign conventions assumed by these tests (verified empirically from a full
day of Solarman 5-minute samples):
    TPG    — always >= 0, total PV DC input
    PG_Pt1 — positive = grid export, negative = grid import
    B_P1   — positive = battery charging, negative = battery discharging
    E_Puse_t1 — always >= 0, home consumption
"""

from __future__ import annotations

from pytest import approx as pytest_approx

from custom_components.solarman_api.const import (
    DERIVED_SELF_CONSUMPTION_POWER,
    DERIVED_SELF_CONSUMPTION_TOTAL,
)
from custom_components.solarman_api.sensor import _derive_value


def test_self_consumption_power_idle_inverter() -> None:
    """No solar, no load, no grid, no battery flow — inverter still burns
    a few watts running its own electronics.
    """
    bucket = {"TPG": "0", "PG_Pt1": "0", "B_P1": "0", "E_Puse_t1": "0"}
    assert _derive_value(DERIVED_SELF_CONSUMPTION_POWER, bucket) == 0.0


def test_self_consumption_power_nighttime_battery_powering_house() -> None:
    """Observed at 22:00 UTC: battery discharging 910 W, home pulls 840 W,
    grid and PV both zero. The 70 W gap is the inverter's own overhead.
    """
    bucket = {"TPG": "0", "PG_Pt1": "0", "B_P1": "-910", "E_Puse_t1": "840"}
    assert _derive_value(DERIVED_SELF_CONSUMPTION_POWER, bucket) == pytest_approx(70)


def test_self_consumption_power_nighttime_grid_import() -> None:
    """At 00:00 UTC: battery discharging 720 W, grid importing 50 W
    (PG_Pt1 = -50), home pulls 690 W, no solar. 80 W inverter overhead.
    """
    bucket = {"TPG": "0", "PG_Pt1": "-50", "B_P1": "-720", "E_Puse_t1": "690"}
    assert _derive_value(DERIVED_SELF_CONSUMPTION_POWER, bucket) == pytest_approx(80)


def test_self_consumption_power_peak_solar_export() -> None:
    """At 11:00 UTC: 3900 W PV, battery barely charging (40 W), exporting
    3110 W to grid (PG_Pt1 = +3110), 590 W home load. Overhead ~160 W.
    """
    bucket = {
        "TPG": "3900",
        "PG_Pt1": "3110",
        "B_P1": "40",
        "E_Puse_t1": "590",
    }
    assert _derive_value(DERIVED_SELF_CONSUMPTION_POWER, bucket) == pytest_approx(160)


def test_self_consumption_power_sign_flip_would_produce_absurd_value() -> None:
    """Regression guard: if PG_Pt1's export-sign convention flipped back
    to the initial (wrong) guess of +=import, the same peak-solar snapshot
    produces an absurd ~6400 W self-consumption. The formula has to treat
    a positive PG_Pt1 as export, i.e. outflow from the inverter.
    """
    bucket = {
        "TPG": "3900",
        "PG_Pt1": "3110",
        "B_P1": "40",
        "E_Puse_t1": "590",
    }
    result = _derive_value(DERIVED_SELF_CONSUMPTION_POWER, bucket)
    assert result is not None
    assert result < 500, f"self-consumption should be a few hundred W, not {result}"


def test_self_consumption_power_missing_field_returns_none() -> None:
    """Any required input missing — sensor goes `unknown` rather than
    producing a fabricated number from partial data.
    """
    # Missing battery power
    bucket = {"TPG": "100", "PG_Pt1": "0", "E_Puse_t1": "80"}
    assert _derive_value(DERIVED_SELF_CONSUMPTION_POWER, bucket) is None


def test_self_consumption_power_empty_string_field_returns_none() -> None:
    bucket = {"TPG": "", "PG_Pt1": "0", "B_P1": "0", "E_Puse_t1": "0"}
    assert _derive_value(DERIVED_SELF_CONSUMPTION_POWER, bucket) is None


def test_self_consumption_total_positive_balance() -> None:
    """Normal steady-state: total inflows exceed outflows by the inverter's
    lifetime self-consumption.
    """
    bucket = {
        "Et_ge0": "43977.7",
        "Et_pu1": "15723.1",
        "t_dcg_n1": "13578.3",
        "Et_use1": "32693.3",
        "t_gc1": "15426.7",
        "t_cg_n1": "14482.7",
    }
    # (43977.7 + 15723.1 + 13578.3) - (32693.3 + 15426.7 + 14482.7) = 10676.4
    assert _derive_value(DERIVED_SELF_CONSUMPTION_TOTAL, bucket) == pytest_approx(
        10676.4
    )


def test_self_consumption_total_clamps_negative_to_zero() -> None:
    """Per-poll counter rounding can push the balance a few Wh negative.
    If we published that, HA's TOTAL_INCREASING compile would detect a
    "reset" and dump the running sum — so clamp to >= 0.
    """
    bucket = {
        "Et_ge0": "100.0",
        "Et_pu1": "0",
        "t_dcg_n1": "0",
        "Et_use1": "100.05",
        "t_gc1": "0",
        "t_cg_n1": "0",
    }
    assert _derive_value(DERIVED_SELF_CONSUMPTION_TOTAL, bucket) == 0.0


def test_self_consumption_total_missing_counter_returns_none() -> None:
    bucket = {
        "Et_ge0": "100",
        "Et_pu1": "0",
        "t_dcg_n1": "0",
        # Et_use1 missing
        "t_gc1": "0",
        "t_cg_n1": "0",
    }
    assert _derive_value(DERIVED_SELF_CONSUMPTION_TOTAL, bucket) is None


def test_unknown_derived_key_returns_none() -> None:
    """Defensive: an unexpected derived key (refactor drift, typo) should
    surface as `unknown` on the entity rather than crash native_value.
    """
    assert _derive_value("_derived_bogus", {"TPG": "0"}) is None
