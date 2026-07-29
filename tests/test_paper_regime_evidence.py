"""Point-in-time tests for forward-only paper regime evidence."""
import math

import pandas as pd

from scripts.phoenix_paper_regime_evidence import classify


def _row(**values):
    base = {
        "timestamp": "2026-07-29T14:00:00+00:00",
        "ret_fast_3bar_pct": 1.0,
        "sector_rs_qqq_pct": 0.7,
        "sector_rs_smh_pct": 0.6,
        "sector_rs_soxx_pct": 0.5,
    }
    base.update(values)
    return pd.Series(base)


def test_regime_uses_only_signal_time_relative_returns():
    result = classify(_row(), threshold_pct=0.20)
    assert result["point_in_time_valid"] is True
    assert result["regime"] == "RISK_ON"
    assert math.isclose(result["qqq_return_10m_pct"], 0.3)
    assert math.isclose(result["semi_return_10m_pct"], 0.45)


def test_naive_timestamp_fails_closed():
    result = classify(
        _row(timestamp="2026-07-29T14:00:00"),
        threshold_pct=0.20,
    )
    assert result["point_in_time_valid"] is False
    assert result["regime"] == "UNKNOWN"


def test_missing_benchmark_input_is_not_imputed():
    result = classify(
        _row(sector_rs_soxx_pct=None),
        threshold_pct=0.20,
    )
    assert result["point_in_time_valid"] is False
    assert result["regime"] == "UNKNOWN"
