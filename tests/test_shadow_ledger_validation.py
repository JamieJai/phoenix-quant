"""Network-free durable shadow portfolio tests."""
from scripts.phoenix_shadow_ledger_validation import validate


def test_shadow_ledger_restart_idempotency_and_exit_contract():
    result = validate()
    assert result["status"] == "PASS"
    assert all(result["checks"].values())
    assert result["network_called"] is False
    assert result["broker_routes_called"] is False
    assert result["live_enabled"] is False
