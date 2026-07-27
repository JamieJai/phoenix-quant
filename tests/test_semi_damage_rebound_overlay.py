"""Frozen-formula and point-in-time tests for the prospective semi overlay."""
from datetime import datetime, timezone

from phoenix_core.features.semi_damage_rebound_overlay import (
    SemiDamageReboundInput,
    evaluate_semi_damage_rebound,
)


SOURCE_CLOSE = datetime(2026, 7, 27, 20, 0, tzinfo=timezone.utc)
NEXT_OPEN = datetime(2026, 7, 28, 13, 30, tzinfo=timezone.utc)


def _input(**overrides):
    values = {
        "as_of_close_utc": SOURCE_CLOSE,
        "available_from_utc": NEXT_OPEN,
        "prediction_timestamp_utc": NEXT_OPEN,
        "qqq_return_20d": -0.044878450256748414,
        "smh_return_20d": -0.11884499726967157,
        "soxx_return_20d": -0.15705374364082902,
        "smh_return_5d": 0.008373264512642198,
        "soxx_return_5d": 0.009965336485235454,
    }
    values.update(overrides)
    return SemiDamageReboundInput(**values)


def test_frozen_formula_matches_preregistration():
    result = evaluate_semi_damage_rebound(_input())
    expected_semi = (-0.11884499726967157 - 0.15705374364082902) / 2.0
    expected_damage = min(1.0, (-0.044878450256748414 - expected_semi) / 0.10)
    assert abs(result.semi_20d - expected_semi) < 1e-12
    assert abs(result.damage - expected_damage) < 1e-12
    assert result.rebound == 1
    assert abs(result.penalty_points - (-5.0 * expected_damage)) < 1e-12
    assert result.runtime_enabled is False


def test_missing_data_is_neutral_and_downgrades_confidence():
    result = evaluate_semi_damage_rebound(_input(soxx_return_5d=None))
    assert result.penalty_points == 0.0
    assert result.confidence_adjustment_points == -10.0
    assert result.status == "MISSING_DATA_NEUTRAL_PENALTY"


def test_same_session_retroactive_application_is_rejected():
    try:
        evaluate_semi_damage_rebound(
            _input(prediction_timestamp_utc=SOURCE_CLOSE)
        )
    except ValueError as exc:
        assert "same-session" in str(exc)
    else:
        raise AssertionError("same-session application must fail")


def test_penalty_is_bounded_and_requires_positive_rebound():
    no_rebound = evaluate_semi_damage_rebound(
        _input(smh_return_5d=-0.01, soxx_return_5d=0.0)
    )
    full_damage = evaluate_semi_damage_rebound(
        _input(
            qqq_return_20d=0.05,
            smh_return_20d=-0.20,
            soxx_return_20d=-0.20,
        )
    )
    assert no_rebound.penalty_points == 0.0
    assert full_damage.penalty_points == -5.0
