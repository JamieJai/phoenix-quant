"""Network-free tests for prospective base OOS non-degradation evidence."""
import pandas as pd

from scripts.phoenix_paper_base_oos_evidence import evaluate


def _prereg():
    return {
        "prospective_market_date_start": "2026-07-29",
        "selection": {"cost_fraction_roundtrip": 0.0014},
        "minimum_evidence": {
            "prospective_mature_rows": 2,
            "prospective_market_dates": 1,
            "base_selected_rows": 1,
            "overlay_selected_rows": 1,
        },
        "acceptance_gate": {
            "overlay_minus_base_net_mean_min": -0.0002,
            "overlay_minus_base_hit_rate_min": -0.02,
        },
    }


def test_identical_scores_pass_non_degradation():
    frame = pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "timestamp": "2026-07-29T14:00:00+00:00",
                "intraday_score": 90,
                "overlay_score": 90,
                "forward_return_5m": 0.01,
            },
            {
                "ticker": "BBB",
                "timestamp": "2026-07-29T14:00:00+00:00",
                "intraday_score": 10,
                "overlay_score": 10,
                "forward_return_5m": -0.01,
            },
        ]
    )
    result = evaluate(frame, _prereg())
    assert result["status"] == "PASS"
    assert result["historical_rows_used_for_gate"] is False


def test_naive_and_prestart_rows_are_excluded():
    frame = pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "timestamp": "2026-07-29T14:00:00",
                "intraday_score": 90,
                "overlay_score": 90,
                "forward_return_5m": 0.01,
            },
            {
                "ticker": "BBB",
                "timestamp": "2026-07-28T14:00:00+00:00",
                "intraday_score": 10,
                "overlay_score": 10,
                "forward_return_5m": -0.01,
            },
        ]
    )
    result = evaluate(frame, _prereg())
    assert result["status"] == "COLLECTING"
    assert result["prospective_mature_rows"] == 0
