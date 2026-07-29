"""Network-free validation tests for the paper kill switch."""
import json
from pathlib import Path

from scripts.phoenix_kill_switch_validation import validate, write_evidence
from scripts.phoenix_live_readiness import _kill_switch_evidence


def test_kill_switch_validation_passes_without_network_or_broker():
    result = validate()
    assert result["status"] == "PASS"
    assert result["network_called"] is False
    assert result["broker_routes_called"] is False
    assert result["account_endpoints_called"] is False
    assert result["paper_fill_count"] == result["expected_paper_fill_count"] == 1
    assert all(result["checks"].values())


def test_live_readiness_accepts_only_safe_evidence(tmp_path: Path):
    result = validate()
    _, latest = write_evidence(result, tmp_path)
    passed, _ = _kill_switch_evidence(str(latest))
    assert passed is True

    unsafe = json.loads(latest.read_text(encoding="utf-8"))
    unsafe["broker_routes_called"] = True
    latest.write_text(json.dumps(unsafe), encoding="utf-8")
    passed, _ = _kill_switch_evidence(str(latest))
    assert passed is False
