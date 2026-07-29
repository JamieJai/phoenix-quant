#!/usr/bin/env python3
"""Research-only readiness audit for staged paper -> shadow -> live review.

This command can never enable a broker or submit an order.  It reports the
remaining evidence required by ``config/paper_trading.yaml``.
"""
from __future__ import annotations

import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

from scripts.phoenix_paper_pnl_report import run as paper_pnl


DEFAULT_KILL_SWITCH_EVIDENCE = (
    "reports/paper_trading/kill_switch/kill_switch_validation_latest.json"
)
DEFAULT_REGIME_EVIDENCE = (
    "reports/paper_trading/regime_evidence/"
    "paper_regime_evidence_latest.json"
)
DEFAULT_BASE_OOS_EVIDENCE = (
    "reports/paper_trading/base_oos/"
    "paper_base_oos_non_degradation_latest.json"
)
DEFAULT_PORTFOLIO_RISK_EVIDENCE = (
    "reports/paper_trading/risk/portfolio_risk_validation_latest.json"
)
DEFAULT_CALIBRATION_EVIDENCE = (
    "reports/paper_trading/calibration/paper_calibration_latest.json"
)


def _config(path: str) -> dict:
    if yaml:
        with Path(path).open(encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return {}


def _kill_switch_evidence(path: str) -> tuple[bool, str]:
    evidence_path = Path(path)
    if not evidence_path.exists():
        return False, "not evidenced"
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "invalid evidence"
    checks = evidence.get("checks", {})
    safe = (
        evidence.get("status") == "PASS"
        and bool(checks)
        and all(value is True for value in checks.values())
        and evidence.get("network_called") is False
        and evidence.get("broker_routes_called") is False
        and evidence.get("account_endpoints_called") is False
        and evidence.get("live_enabled") is False
        and evidence.get("production_changed") is False
    )
    return safe, str(evidence.get("validated_at_utc", "invalid evidence"))


def _regime_evidence(path: str) -> tuple[bool, str]:
    evidence_path = Path(path)
    if not evidence_path.exists():
        return False, "not evidenced"
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "invalid evidence"
    safe = (
        evidence.get("status") == "PASS"
        and int(evidence.get("qualifying_regime_count", 0) or 0) >= 2
        and evidence.get("historical_rows_used_for_gate") is False
        and evidence.get("production_score_changed") is False
        and evidence.get("champion_changed") is False
        and evidence.get("paper_signal_changed") is False
        and evidence.get("broker_routes_called") is False
        and evidence.get("live_enabled") is False
    )
    regimes = ",".join(evidence.get("qualifying_regimes", []))
    return safe, regimes or "not enough prospective regimes"


def _base_oos_evidence(path: str) -> tuple[bool, str]:
    evidence_path = Path(path)
    if not evidence_path.exists():
        return False, "not evidenced"
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "invalid evidence"
    safe = (
        evidence.get("status") == "PASS"
        and evidence.get("minimum_evidence_met") is True
        and evidence.get("historical_rows_used_for_gate") is False
        and evidence.get("production_score_changed") is False
        and evidence.get("champion_changed") is False
        and evidence.get("paper_signal_changed") is False
        and evidence.get("broker_routes_called") is False
        and evidence.get("live_enabled") is False
    )
    value = (
        f"net_delta={evidence.get('overlay_minus_base_net_mean')},"
        f"hit_delta={evidence.get('overlay_minus_base_hit_rate')}"
        if evidence.get("minimum_evidence_met")
        else "not enough prospective evidence"
    )
    return safe, value


def _portfolio_risk_evidence(path: str) -> tuple[bool, str]:
    evidence_path = Path(path)
    if not evidence_path.exists():
        return False, "not evidenced"
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "invalid evidence"
    checks = evidence.get("checks", {})
    safe = (
        evidence.get("status") == "PASS"
        and bool(checks)
        and all(value is True for value in checks.values())
        and evidence.get("network_called") is False
        and evidence.get("broker_routes_called") is False
        and evidence.get("account_endpoints_called") is False
        and evidence.get("live_enabled") is False
        and evidence.get("production_changed") is False
        and evidence.get("paper_route_changed") is False
    )
    return safe, str(evidence.get("validated_at_utc", "invalid evidence"))


def _calibration_evidence(
    path: str,
    *,
    minimum_samples: int,
) -> tuple[bool, str]:
    evidence_path = Path(path)
    if not evidence_path.exists():
        return False, "not evidenced"
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "invalid evidence"
    coverage = evidence.get("coverage", {})
    paired = int(coverage.get("paired_prediction_realized", 0) or 0)
    realized = int(coverage.get("realized_net_return", 0) or 0)
    slippage = int(
        coverage.get("actual_or_paper_fill_slippage", 0) or 0
    )
    safe = (
        evidence.get("status") == "CALIBRATION_READY"
        and paired >= minimum_samples
        and realized >= minimum_samples
        and slippage >= minimum_samples
        and evidence.get("parameter_retuning_allowed") is False
        and evidence.get("broker_routes_called") is False
        and evidence.get("account_endpoints_called") is False
        and evidence.get("live_enabled") is False
        and evidence.get("parameter_changes_applied") is False
    )
    return (
        safe,
        f"paired={paired},realized={realized},slippage={slippage}",
    )


def audit(*, cache: str = "data/intraday_features.csv",
          config_path: str = "config/paper_trading.yaml",
          pnl_path: str | None = None,
          kill_switch_evidence: str = DEFAULT_KILL_SWITCH_EVIDENCE,
          regime_evidence: str = DEFAULT_REGIME_EVIDENCE,
          base_oos_evidence: str = DEFAULT_BASE_OOS_EVIDENCE,
          portfolio_risk_evidence: str = DEFAULT_PORTFOLIO_RISK_EVIDENCE,
          calibration_evidence: str = DEFAULT_CALIBRATION_EVIDENCE) -> dict:
    cfg = _config(config_path)
    gates = cfg.get("promotion_gates", {}).get("live_review", {})
    pnl = json.loads(Path(pnl_path).read_text(encoding="utf-8")) if pnl_path else paper_pnl(cache)
    # Use the most mature horizon available.
    horizons = [pnl.get(h, {}) for h in ("5m", "10m")]
    best = max(horizons, key=lambda x: int(x.get("mature", 0) or 0), default={})
    mature = int(best.get("mature", 0) or 0)
    # Cache timestamps provide an auditable observed span (calendar days).
    days = 0.0
    try:
        import csv
        from datetime import datetime, timezone
        with Path(cache).open(newline="", encoding="utf-8") as fh:
            ts = []
            for row in csv.DictReader(fh):
                raw = row.get("timestamp") or row.get("recorded_at")
                if raw:
                    try:
                        t = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                        if t.tzinfo is None: t = t.replace(tzinfo=timezone.utc)
                        ts.append(t)
                    except ValueError: pass
            if ts: days = (max(ts) - min(ts)).total_seconds() / 86400.0
    except (OSError, csv.Error):
        pass
    kill_switch_passed, kill_switch_value = _kill_switch_evidence(
        kill_switch_evidence
    )
    regime_passed, regime_value = _regime_evidence(regime_evidence)
    base_oos_passed, base_oos_value = _base_oos_evidence(base_oos_evidence)
    portfolio_risk_passed, portfolio_risk_value = _portfolio_risk_evidence(
        portfolio_risk_evidence
    )
    minimum_calibration_samples = int(
        gates.get("min_calibrated_predictions", 100)
    )
    calibration_passed, calibration_value = _calibration_evidence(
        calibration_evidence,
        minimum_samples=minimum_calibration_samples,
    )
    checks = {
        "mature_trades": (mature >= int(gates.get("min_mature_trades", 500)), mature, gates.get("min_mature_trades", 500)),
        "observed_days": (days >= float(gates.get("min_days", 60)), round(days, 2), gates.get("min_days", 60)),
        "cost_adjusted_positive_expectancy": (float(best.get("avg_net_return") or 0) > 0, best.get("avg_net_return"), "> 0"),
        "two_market_regimes": (
            regime_passed,
            regime_value,
            "2 prospective regimes with >=20 mature outcomes each",
        ),
        "base_oos_non_degradation": (
            base_oos_passed,
            base_oos_value,
            "frozen prospective identical-window ablation",
        ),
        "manual_signoff": (False, "not granted", "required"),
        "kill_switch_test": (
            kill_switch_passed,
            kill_switch_value,
            "validated paper-engine evidence",
        ),
        "portfolio_risk_limits": (
            portfolio_risk_passed,
            portfolio_risk_value,
            "validated paper-engine evidence",
        ),
        "cost_slippage_audit": (bool(pnl.get("roundtrip_cost_pct") is not None), pnl.get("roundtrip_cost_pct"), "recorded"),
        "prediction_calibration": (
            calibration_passed,
            calibration_value,
            f">={minimum_calibration_samples} paired predicted/realized outcomes",
        ),
    }
    reasons = [f"{k}: {v[1]} (need {v[2]})" for k, v in checks.items() if not v[0]]
    return {"status": "LIVE_BLOCKED", "live_enabled": False, "broker_enabled": False,
            "best_horizon": best, "observed_days": round(days, 2),
            "kill_switch_evidence": kill_switch_evidence,
            "regime_evidence": regime_evidence,
            "base_oos_evidence": base_oos_evidence,
            "portfolio_risk_evidence": portfolio_risk_evidence,
            "calibration_evidence": calibration_evidence,
            "checks": {k: {"passed": v[0], "value": v[1], "requirement": v[2]} for k, v in checks.items()},
            "blocking_reasons": reasons}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", default="data/intraday_features.csv")
    ap.add_argument("--config", default="config/paper_trading.yaml")
    ap.add_argument("--pnl-json")
    ap.add_argument(
        "--kill-switch-evidence",
        default=DEFAULT_KILL_SWITCH_EVIDENCE,
    )
    ap.add_argument(
        "--regime-evidence",
        default=DEFAULT_REGIME_EVIDENCE,
    )
    ap.add_argument(
        "--base-oos-evidence",
        default=DEFAULT_BASE_OOS_EVIDENCE,
    )
    ap.add_argument(
        "--portfolio-risk-evidence",
        default=DEFAULT_PORTFOLIO_RISK_EVIDENCE,
    )
    ap.add_argument(
        "--calibration-evidence",
        default=DEFAULT_CALIBRATION_EVIDENCE,
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    out = audit(
        cache=args.cache,
        config_path=args.config,
        pnl_path=args.pnl_json,
        kill_switch_evidence=args.kill_switch_evidence,
        regime_evidence=args.regime_evidence,
        base_oos_evidence=args.base_oos_evidence,
        portfolio_risk_evidence=args.portfolio_risk_evidence,
        calibration_evidence=args.calibration_evidence,
    )
    print(json.dumps(out, ensure_ascii=False) if args.json else f"{out['status']}: " + "; ".join(out["blocking_reasons"]))


if __name__ == "__main__":
    main()
