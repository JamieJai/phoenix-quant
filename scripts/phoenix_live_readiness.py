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


def _config(path: str) -> dict:
    if yaml:
        with Path(path).open(encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return {}


def audit(*, cache: str = "data/intraday_features.csv",
          config_path: str = "config/paper_trading.yaml",
          pnl_path: str | None = None) -> dict:
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
    checks = {
        "mature_trades": (mature >= int(gates.get("min_mature_trades", 500)), mature, gates.get("min_mature_trades", 500)),
        "observed_days": (days >= float(gates.get("min_days", 60)), round(days, 2), gates.get("min_days", 60)),
        "cost_adjusted_positive_expectancy": (float(best.get("avg_net_return") or 0) > 0, best.get("avg_net_return"), "> 0"),
        "two_market_regimes": (False, "not evidenced", "required"),
        "base_oos_non_degradation": (False, "not evidenced", "required"),
        "manual_signoff": (False, "not granted", "required"),
        "kill_switch_test": (False, "not evidenced", "required"),
        "cost_slippage_audit": (bool(pnl.get("roundtrip_cost_pct") is not None), pnl.get("roundtrip_cost_pct"), "recorded"),
    }
    reasons = [f"{k}: {v[1]} (need {v[2]})" for k, v in checks.items() if not v[0]]
    return {"status": "LIVE_BLOCKED", "live_enabled": False, "broker_enabled": False,
            "best_horizon": best, "observed_days": round(days, 2),
            "checks": {k: {"passed": v[0], "value": v[1], "requirement": v[2]} for k, v in checks.items()},
            "blocking_reasons": reasons}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", default="data/intraday_features.csv")
    ap.add_argument("--config", default="config/paper_trading.yaml")
    ap.add_argument("--pnl-json")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    out = audit(cache=args.cache, config_path=args.config, pnl_path=args.pnl_json)
    print(json.dumps(out, ensure_ascii=False) if args.json else f"{out['status']}: " + "; ".join(out["blocking_reasons"]))


if __name__ == "__main__":
    main()
