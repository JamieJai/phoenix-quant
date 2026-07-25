#!/usr/bin/env python3
"""Replay intraday cache rows through the research-only paper engine.

This command never contacts a broker.  It is intended to exercise execution
gates (freshness, confidence, R:R and position sizing) against observed rows.
"""
from __future__ import annotations

import argparse
import sys
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from typing import Any

from phoenix_core.trade.paper_engine import (
    OrderSide, PaperEngineConfig, PaperSignal, PaperTradingEngine,
)


def _float(row: dict[str, str], *names: str, default: float | None = None) -> float | None:
    for name in names:
        try:
            value = float(row.get(name, ""))
            if value == value:
                return value
        except (TypeError, ValueError):
            pass
    return default


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (result if result.tzinfo else result.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except ValueError:
        return None


def run(path: str, *, limit: int = 0, equity: float = 100_000.0,
        quantity: float = 1.0, rr: float = 2.0, replay: bool = False,
        max_age: int = 300) -> dict[str, Any]:
    config = PaperEngineConfig(max_data_age_seconds=max_age)
    engine = PaperTradingEngine(config, equity=equity)
    rows: list[dict[str, str]] = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if limit > 0:
        rows = rows[-limit:]
    accepted = rejected = 0
    rejection_reasons: dict[str, int] = {}
    fills: list[dict[str, Any]] = []
    for row in rows:
        ts = _dt(row.get("timestamp") or row.get("recorded_at"))
        price = _float(row, "current_price", "close")
        confidence = _float(row, "data_confidence_score", "confidence_score")
        if confidence is None:
            required = ("current_price", "previous_close", "ret_fast_3bar_pct", "ret_slow_2bar_pct", "relative_intraday_volume", "vwap_position_pct")
            present = sum(_float(row, name) is not None for name in required)
            confidence = present / len(required) * 70.0 + 20.0
        rr_value = _float(row, "rr_ratio", default=rr) or rr
        ticker = (row.get("ticker") or row.get("symbol") or "").strip()
        if not ticker or price is None or ts is None:
            rejected += 1
            rejection_reasons["invalid_order"] = rejection_reasons.get("invalid_order", 0) + 1
            continue
        signal = PaperSignal(symbol=ticker, side=OrderSide.BUY, price=price,
                             quantity=quantity, confidence=confidence, rr_ratio=rr_value,
                             timestamp=ts, data_timestamp=ts,
                             metadata={"risk_fraction": config.max_loss_per_trade,
                                       "source": "intraday_features.csv",
                                       "state": row.get("label", "")})
        # Historical replay evaluates freshness at the observation timestamp.
        now = ts if replay else datetime.now(timezone.utc)
        gate = engine.check_gates(signal, now=now)
        if not gate.allowed:
            rejected += 1
            for reason in gate.reasons:
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
            continue
        fill = engine.submit(signal, now=now)
        if fill is None:
            rejected += 1
            rejection_reasons["submit_rejected"] = rejection_reasons.get("submit_rejected", 0) + 1
        else:
            accepted += 1
            fills.append({"symbol": fill.symbol, "price": fill.price, "quantity": fill.quantity,
                          "fee": fill.fee, "slippage": fill.slippage, "filled_at": fill.filled_at.isoformat()})
    return {"status": "PAPER_ONLY", "path": path, "rows": len(rows), "accepted": accepted,
            "rejected": rejected, "rejection_reasons": rejection_reasons,
            "fills": fills, "audit_events": len(engine.audit_log), "live_broker": False}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default="data/intraday_features.csv")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--quantity", type=float, default=1.0)
    ap.add_argument("--equity", type=float, default=100_000.0)
    ap.add_argument("--rr", type=float, default=2.0, help="fallback R:R when cache has no rr_ratio")
    ap.add_argument("--max-age", type=int, default=300)
    ap.add_argument("--replay", action="store_true", help="evaluate each row at its observation time")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    result = run(args.path, limit=args.limit, equity=args.equity, quantity=args.quantity,
                 rr=args.rr, replay=args.replay, max_age=args.max_age)
    print(json.dumps(result, ensure_ascii=False, default=str) if args.json else
          f"PAPER_ONLY rows={result['rows']} accepted={result['accepted']} rejected={result['rejected']}")


if __name__ == "__main__":
    main()
