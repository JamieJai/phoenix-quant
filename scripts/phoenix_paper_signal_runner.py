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
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from typing import Any
import yaml

from phoenix_core.trade.paper_engine import (
    OrderSide, PaperEngineConfig, PaperSignal, PaperTradingEngine,
)

NY = ZoneInfo("America/New_York")


def paper_config(
    path: str,
    *,
    equity: float,
    max_age_override: int | None = None,
) -> PaperEngineConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    execution = raw.get("execution", {})
    limits = raw.get("risk_limits", {})
    gates = raw.get("signal_gates", {})
    max_age = (
        max_age_override
        if max_age_override is not None
        else int(execution.get("reject_if_stale_seconds", 30))
    )
    return PaperEngineConfig(
        min_confidence=float(gates.get("min_confidence_score", 70.0)),
        min_rr_ratio=float(gates.get("min_rr_ratio", 1.5)),
        max_loss_per_trade=(
            float(limits.get("max_risk_per_trade_pct", 0.5)) / 100.0
        ),
        fee_bps=float(execution.get("commission_bps", 2.0)),
        slippage_bps=float(execution.get("slippage_bps", 5.0)),
        max_data_age_seconds=max_age,
        max_position_value=(
            equity
            * float(limits.get("max_notional_per_position_pct", 20.0))
            / 100.0
        ),
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
        max_age: int | None = None,
        config_path: str = "config/paper_trading.yaml",
        calibrator_path: str | None = None) -> dict[str, Any]:
    config = paper_config(
        config_path,
        equity=equity,
        max_age_override=max_age,
    )
    engine = PaperTradingEngine(config, equity=equity)
    calibrator = None
    if calibrator_path:
        calibrator = json.loads(
            Path(calibrator_path).read_text(encoding="utf-8")
        )
        if (
            calibrator.get("status") != "FROZEN"
            or calibrator.get("model_type")
            != "CONSTANT_HISTORICAL_MEAN_GROSS_RETURN"
            or calibrator.get("selection_or_order_use") is not False
        ):
            raise ValueError("invalid research-only calibration artifact")
    rows: list[dict[str, str]] = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if limit > 0:
        rows = rows[-limit:]
    accepted = rejected = 0
    rejection_reasons: dict[str, int] = {}
    fills: list[dict[str, Any]] = []
    for source_row, row in enumerate(rows, start=1):
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
            predicted_return = _float(
                row,
                "predicted_return",
                "predicted_return_5m",
                "model_expected_return",
            )
            prediction_source = "SOURCE_ROW" if predicted_return is not None else ""
            if calibrator is not None and predicted_return is None:
                market_date = ts.astimezone(NY).date().isoformat()
                if market_date >= str(
                    calibrator["prospective_market_date_start"]
                ):
                    predicted_return = float(
                        calibrator["predicted_return"]
                    )
                    prediction_source = str(
                        calibrator["calibrator_id"]
                    )
            realized_gross_return = _float(
                row,
                "forward_return_5m",
                "realized_gross_return",
            )
            fills.append(
                {
                    "source_row": source_row,
                    "symbol": fill.symbol,
                    "signal_timestamp": ts.isoformat(),
                    "data_timestamp": ts.isoformat(),
                    "filled_at": fill.filled_at.isoformat(),
                    "side": fill.side.value,
                    "quantity": fill.quantity,
                    "signal_price": signal.price,
                    "fill_price": fill.price,
                    "notional": fill.price * fill.quantity,
                    "fee": fill.fee,
                    "fee_bps": config.fee_bps,
                    "slippage_notional": fill.slippage,
                    "paper_fill_slippage_bps": (
                        abs(fill.price / signal.price - 1.0) * 10_000.0
                    ),
                    "estimated_slippage_bps": config.slippage_bps,
                    "predicted_return": predicted_return,
                    "predicted_return_source": prediction_source,
                    "forward_return_5m": realized_gross_return,
                    "source": row.get("source", ""),
                    "state": row.get("label", ""),
                    "live_broker": False,
                }
            )
    return {"status": "PAPER_ONLY", "path": path, "rows": len(rows), "accepted": accepted,
            "rejected": rejected, "rejection_reasons": rejection_reasons,
            "fills": fills, "audit_events": len(engine.audit_log),
            "execution_assumptions": {
                "config_path": config_path,
                "fee_bps": config.fee_bps,
                "slippage_bps": config.slippage_bps,
                "min_confidence": config.min_confidence,
                "min_rr_ratio": config.min_rr_ratio,
                "max_loss_per_trade": config.max_loss_per_trade,
                "max_position_value": config.max_position_value,
                "max_data_age_seconds": config.max_data_age_seconds,
                "equity": equity,
                "quantity": quantity,
                "portfolio_stateful_replay": False,
            },
            "live_broker": False, "broker_routes_called": False}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def persist(result: dict[str, Any], *, output_json: str | None = None,
            fills_csv: str | None = None) -> dict[str, dict[str, str]]:
    artifacts: dict[str, dict[str, str]] = {}
    if output_json:
        path = Path(output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(result, ensure_ascii=False, default=str, indent=2) + "\n",
            encoding="utf-8",
        )
        artifacts["json"] = {"path": str(path), "sha256": _sha256(path)}
    if fills_csv:
        path = Path(fills_csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        fills = result.get("fills", [])
        fieldnames = list(fills[0]) if fills else [
            "source_row", "symbol", "signal_timestamp", "data_timestamp",
            "filled_at", "side", "quantity", "signal_price", "fill_price",
            "notional", "fee", "fee_bps", "slippage_notional",
            "paper_fill_slippage_bps", "estimated_slippage_bps",
            "predicted_return", "forward_return_5m", "source", "state",
            "predicted_return_source",
            "live_broker",
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(fills)
        artifacts["fills_csv"] = {"path": str(path), "sha256": _sha256(path)}
    return artifacts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default="data/intraday_features.csv")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--quantity", type=float, default=1.0)
    ap.add_argument("--equity", type=float, default=100_000.0)
    ap.add_argument("--rr", type=float, default=2.0, help="fallback R:R when cache has no rr_ratio")
    ap.add_argument("--max-age", type=int)
    ap.add_argument("--config", default="config/paper_trading.yaml")
    ap.add_argument("--calibrator")
    ap.add_argument("--replay", action="store_true", help="evaluate each row at its observation time")
    ap.add_argument("--output-json")
    ap.add_argument("--fills-csv")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    result = run(args.path, limit=args.limit, equity=args.equity, quantity=args.quantity,
                 rr=args.rr, replay=args.replay, max_age=args.max_age,
                 config_path=args.config,
                 calibrator_path=args.calibrator)
    artifacts = persist(
        result,
        output_json=args.output_json,
        fills_csv=args.fills_csv,
    )
    if artifacts:
        result["artifacts"] = artifacts
    print(json.dumps(result, ensure_ascii=False, default=str) if args.json else
          f"PAPER_ONLY rows={result['rows']} accepted={result['accepted']} rejected={result['rejected']}")


if __name__ == "__main__":
    main()
