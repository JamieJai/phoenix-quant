"""Fail-closed live-readiness checks for stateful shadow evidence."""
import json
from pathlib import Path

from scripts.phoenix_live_readiness import (
    _shadow_control_evidence,
    _shadow_execution_evidence,
)
from scripts.phoenix_shadow_ledger_validation import validate


def test_shadow_control_evidence_requires_safe_offline_validation(
    tmp_path: Path,
):
    evidence = validate()
    path = tmp_path / "control.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    passed, _ = _shadow_control_evidence(str(path))
    assert passed is True
    evidence["broker_routes_called"] = True
    path.write_text(json.dumps(evidence), encoding="utf-8")
    passed, _ = _shadow_control_evidence(str(path))
    assert passed is False


def test_shadow_execution_requires_frozen_sample_and_time_gates(
    tmp_path: Path,
):
    path = tmp_path / "execution.json"
    path.write_text(
        json.dumps(
            {
                "status": "SHADOW_ACTIVE",
                "ledger": {
                    "closed_positions": 250,
                    "observed_days": 40.0,
                    "mean_net_return": 0.001,
                    "maximum_drawdown_fraction": 0.05,
                    "execution_quote_rejection_rate": 0.10,
                },
                "broker_routes_called": False,
                "account_endpoints_called": False,
                "live_enabled": False,
                "champion_changed": False,
                "production_score_changed": False,
            }
        ),
        encoding="utf-8",
    )
    passed, _ = _shadow_execution_evidence(
        str(path),
        minimum_closed=250,
        minimum_days=40,
        maximum_mdd_fraction=0.10,
        maximum_quote_rejection_rate=0.20,
    )
    assert passed is True
