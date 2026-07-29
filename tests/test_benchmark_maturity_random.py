"""Regression tests for benchmark OOS maturity and random-baseline helpers."""

import pandas as pd

from benchmark import _filter_complete_future_rows, _random_baseline_summary


def _prices(last: str, n: int = 12) -> pd.DataFrame:
    idx = pd.date_range(end=last, periods=n, freq="B")
    return pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1_000_000}, index=idx)


def test_immature_as_of_rows_are_excluded_from_oos():
    raw = {"AAA": _prices("2026-07-20", 20)}
    rows = [
        {"ticker": "AAA", "as_of": "2026-06-22"},  # has 10 subsequent sessions
        {"ticker": "AAA", "as_of": "2026-07-17"},  # only one subsequent session
    ]

    kept = _filter_complete_future_rows(rows, raw, required_days=10)

    assert [r["as_of"] for r in kept] == ["2026-06-22"]


def test_random_baseline_is_seeded_and_reports_lift_inputs():
    rows = []
    for date in ("2026-01-02", "2026-01-05"):
        for ticker, hit in (("AAA", 1.0), ("BBB", 0.0)):
            rows.append(
                {
                    "as_of": date,
                    "ticker": ticker,
                    "hit_5pct_5d": hit,
                    "hit_10pct_10d": hit,
                    "fwd_max_ret_5d": 0.10 if hit else -0.02,
                    "fwd_max_ret_10d": 0.12 if hit else -0.03,
                    "close_hit_5pct_5d": hit,
                    "close_hit_10pct_10d": hit,
                    "fwd_close_ret_5d": 0.05 if hit else -0.01,
                    "fwd_close_ret_10d": 0.06 if hit else -0.02,
                    "fwd_min_ret_5d": -0.01,
                    "fwd_min_ret_10d": -0.02,
                }
            )

    first = _random_baseline_summary(rows, [1], iterations=200, seed=7)
    second = _random_baseline_summary(rows, [1], iterations=200, seed=7)

    assert not first.empty
    assert first.equals(second)
    assert 0.0 <= first.loc[0, "random_hit_5pct_5d_mean"] <= 1.0
    assert first.loc[0, "iterations"] == 200
