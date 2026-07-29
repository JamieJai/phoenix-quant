"""Network-free durable execution interlock tests."""
from scripts.phoenix_execution_interlock_validation import validate


def test_execution_interlock_is_fail_closed_and_broker_free():
    result = validate()
    assert result["status"] == "PASS"
    assert all(result["checks"].values())
    assert result["approval_artifact_created"] is False
    assert result["kill_switch_disarmed"] is False
    assert result["broker_routes_called"] is False
    assert result["live_enabled"] is False
