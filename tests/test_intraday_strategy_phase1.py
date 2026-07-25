"""Focused, network-free checks for the Phase 1 intraday research overlay."""

import pandas as pd

from phoenix_core.engines.intraday_context_engine import IntradayContextEngine


def test_score_is_bounded_and_classifies_extreme_chase_context():
    engine = IntradayContextEngine()
    score, risk, label, notes = engine._score(
        gap=12.0, dayret=8.0, r10=4.0, r30=5.0, vr=5.0, vpos=3.0, pull=-0.2
    )
    assert 0 <= score <= 100
    assert 0 <= risk <= 100
    assert label in {
        "STRONG_INTRADAY_MOMENTUM",
        "POSITIVE_INTRADAY_CONTEXT",
        "MIXED_INTRADAY_CONTEXT",
        "WEAK_INTRADAY_CONTEXT",
    }
    assert notes  # extreme gap/volume should leave an auditable warning


def test_missing_data_does_not_create_positive_signal():
    engine = IntradayContextEngine()
    score, risk, label, _ = engine._score(None, None, None, None, None, None, None)
    assert score == 0
    assert risk == 25
    assert label == "WEAK_INTRADAY_CONTEXT"

    failed = engine._err("MU", "NO_DATA", ["missing"])
    assert failed.intraday_score == 0
    assert failed.intraday_risk_score == 100
    assert failed.current_price is None


def test_chase_penalty_is_bounded_and_increases_with_vwap_distance():
    engine = IntradayContextEngine()
    daily = pd.DataFrame(
        {
            "High": [101.0] * 10,
            "Low": [99.0] * 10,
            "Close": [100.0] * 10,
        }
    )
    normal = engine._chase_penalty(101.0, 100.0, daily)
    extended = engine._chase_penalty(120.0, 100.0, daily)
    assert 0 <= normal <= 12
    assert 0 <= extended <= 12
    assert extended > normal
