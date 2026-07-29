#!/usr/bin/env python3
"""Research-only cost-adjusted PnL report for paper signals.

Replays cache rows through the same paper risk gates, then evaluates only
mature forward returns.  No broker or live order path is used.
"""
from __future__ import annotations

import argparse, csv, json, math, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.phoenix_paper_signal_runner import _dt, _float, paper_config
from phoenix_core.trade.paper_engine import PaperEngineConfig, PaperSignal, PaperTradingEngine, OrderSide


def run(path: str = "data/intraday_features.csv", *, fee_bps: float = 2.0,
        slippage_bps: float = 5.0, min_confidence: float = 50.0,
        min_rr: float = 1.2, max_age: int | None = None, limit: int = 0,
        config_path: str = "config/paper_trading.yaml") -> dict:
    cfg = paper_config(
        config_path,
        equity=100_000.0,
        max_age_override=max_age,
    )
    # Explicit non-default function arguments remain available for bounded
    # synthetic callers; normal operations use the YAML contract above.
    if (fee_bps, slippage_bps, min_confidence, min_rr) != (2.0, 5.0, 50.0, 1.2):
        cfg.fee_bps = fee_bps
        cfg.slippage_bps = slippage_bps
        cfg.min_confidence = min_confidence
        cfg.min_rr_ratio = min_rr
    engine = PaperTradingEngine(cfg)
    with Path(path).open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if limit > 0:
        rows = rows[-limit:]
    accepted = rejected = 0
    mature = []
    reject_reasons: dict[str, int] = {}
    roundtrip_cost = 2.0 * (cfg.fee_bps + cfg.slippage_bps) / 10_000.0
    for row in rows:
        ts = _dt(row.get("timestamp") or row.get("recorded_at"))
        price = _float(row, "current_price", "close")
        conf = _float(row, "data_confidence_score", "confidence_score")
        if conf is None:
            req = ("current_price", "previous_close", "ret_fast_3bar_pct", "ret_slow_2bar_pct",
                   "relative_intraday_volume", "vwap_position_pct")
            conf = sum(_float(row, n) is not None for n in req) / len(req) * 70.0 + 20.0
        rr = _float(row, "rr_ratio", default=2.0) or 2.0
        ticker = (row.get("ticker") or row.get("symbol") or "").strip()
        if not ticker or price is None or ts is None:
            rejected += 1; reject_reasons["invalid_order"] = reject_reasons.get("invalid_order", 0) + 1; continue
        sig = PaperSignal(symbol=ticker, side=OrderSide.BUY, price=price, quantity=1,
                          confidence=conf, rr_ratio=rr, timestamp=ts, data_timestamp=ts,
                          metadata={"risk_fraction": cfg.max_loss_per_trade})
        gate = engine.check_gates(sig, now=ts)
        if not gate.allowed:
            rejected += 1
            for reason in gate.reasons: reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
            continue
        accepted += 1
        for horizon in ("5m", "10m"):
            ret = _float(row, f"forward_return_{horizon}")
            if ret is None or not math.isfinite(ret) or abs(ret) > 1.0:
                continue
            net = ret - roundtrip_cost
            mature.append((horizon, net))
    result = {"status": "PAPER_ONLY", "path": path, "rows": len(rows),
              "accepted": accepted, "rejected": rejected, "rejection_reasons": reject_reasons,
              "roundtrip_cost_pct": roundtrip_cost * 100.0, "live_broker": False}
    for horizon in ("5m", "10m"):
        vals = [v for h, v in mature if h == horizon]
        wins = [v for v in vals if v > 0]
        result[horizon] = {"mature": len(vals), "hit_rate": len(wins) / len(vals) if vals else None,
                          "avg_net_return": sum(vals) / len(vals) if vals else None,
                          "total_net_return": sum(vals) if vals else 0.0,
                          "status": "READY" if len(vals) >= 500 else "EXPERIMENTAL"}
    result["status"] = "READY" if any(result[h]["mature"] >= 500 for h in ("5m", "10m")) else "EXPERIMENTAL"
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default="data/intraday_features.csv"); ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--fee-bps", type=float, default=2.0); ap.add_argument("--slippage-bps", type=float, default=5.0)
    ap.add_argument("--config", default="config/paper_trading.yaml")
    ap.add_argument("--json", action="store_true"); args = ap.parse_args()
    out = run(
        args.path,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        limit=args.limit,
        config_path=args.config,
    )
    print(json.dumps(out, ensure_ascii=False) if args.json else f"{out['status']} accepted={out['accepted']} 5m={out['5m']['mature']}")

if __name__ == "__main__": main()
