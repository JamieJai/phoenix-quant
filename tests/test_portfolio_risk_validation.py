"""Network-free paper portfolio risk validation."""
from scripts.phoenix_portfolio_risk_validation import validate


def test_declared_portfolio_limits_are_enforced():
    result = validate()
    assert result["status"] == "PASS"
    assert all(result["checks"].values())
    assert result["network_called"] is False
    assert result["broker_routes_called"] is False
    assert result["paper_route_changed"] is False
