from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.phoenix_model_gate import _evaluate


def _args():
    return argparse.Namespace(
        min_sample_size=50,
        min_active_trades=30,
        require_leakage_audit=True,
        require_rolling_oos=True,
        min_rolling_splits=2,
        min_rolling_pass_rate=1.0,
        require_data_coverage=True,
        allow_xgb_promotion=False,
        allow_initial_promotion=True,
        max_p_value=0.20,
        min_portfolio_delta=0.001,
        max_mdd_slippage=0.02,
    )


def _candidate():
    return {
        "metrics": {
            "sample_size": 100,
            "portfolio_return_by_date_mean": 0.01,
            "alpha": 0.005,
            "p_value": 0.05,
            "mdd": 0.05,
            "active_trades": 80,
        },
        "promotion_rank_mode": "decision",
        "promotion_xgb_blend_weight": 0.0,
    }


def _rolling():
    return {"passed": True, "summary": {"n_splits": 2, "pass_rate": 1.0}}


def test_gate_rejects_failed_data_coverage():
    promoted, reasons = _evaluate(
        _candidate(),
        None,
        _args(),
        {"passed": True},
        _rolling(),
        {
            "passed": False,
            "failure_reasons": ["main:train_usable_ratio=0.000000<0.900000"],
        },
    )
    assert not promoted
    assert any("data coverage audit failed" in reason for reason in reasons)


def test_gate_accepts_valid_initial_candidate():
    promoted, reasons = _evaluate(
        _candidate(),
        None,
        _args(),
        {"passed": True},
        _rolling(),
        {"passed": True, "failure_reasons": []},
    )
    assert promoted
    assert reasons == []


if __name__ == "__main__":
    test_gate_rejects_failed_data_coverage()
    test_gate_accepts_valid_initial_candidate()
    print("auto learning guard tests passed")
