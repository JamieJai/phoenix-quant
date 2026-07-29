#!/usr/bin/env python3
"""Validate declared portfolio risk limits without broker or network access."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from phoenix_core.trade.paper_engine import (  # noqa: E402
    OrderSide,
    PaperEngineConfig,
    PaperSignal,
    PaperTradingEngine,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _signal(now: datetime, symbol: str, *, side: OrderSide = OrderSide.BUY,
            quantity: float = 10.0, price: float = 100.0) -> PaperSignal:
    return PaperSignal(
        symbol=symbol,
        side=side,
        price=price,
        quantity=quantity,
        confidence=90.0,
        rr_ratio=2.0,
        timestamp=now,
        data_timestamp=now,
        metadata={"risk_fraction": 0.005, "validation_only": True},
    )


def validate(config_path: str = "config/paper_trading.yaml") -> dict[str, object]:
    path = ROOT / config_path
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    limits = config["risk_limits"]
    now = datetime.now(timezone.utc)
    equity = 100_000.0
    engine_config = PaperEngineConfig(
        min_confidence=float(config["signal_gates"]["min_confidence_score"]),
        min_rr_ratio=float(config["signal_gates"]["min_rr_ratio"]),
        max_loss_per_trade=float(limits["max_risk_per_trade_pct"]) / 100.0,
        max_position_value=(
            equity * float(limits["max_notional_per_position_pct"]) / 100.0
        ),
        max_open_positions=int(limits["max_open_positions"]),
        max_gross_exposure_fraction=(
            float(limits["max_gross_exposure_pct"]) / 100.0
        ),
        max_orders_per_day=int(limits["max_orders_per_day"]),
        max_daily_loss_fraction=float(limits["max_daily_loss_pct"]) / 100.0,
        stop_after_consecutive_losses=int(
            limits["stop_trading_after_consecutive_losses"]
        ),
        max_data_age_seconds=int(config["execution"]["reject_if_stale_seconds"]),
        fee_bps=float(config["execution"]["commission_bps"]),
        slippage_bps=float(config["execution"]["slippage_bps"]),
    )

    positions = PaperTradingEngine(engine_config, equity=equity)
    validation_quantity = engine_config.max_position_value / 100.0 * 0.99
    position_fills = [
        positions.submit(
            _signal(now, symbol, quantity=validation_quantity),
            now=now,
        )
        for symbol in ("A", "B", "C")
    ]
    fourth_gate = positions.check_gates(
        _signal(now, "D", quantity=validation_quantity),
        now=now,
    )

    order_engine = PaperTradingEngine(
        PaperEngineConfig(max_orders_per_day=2),
        equity=equity,
    )
    order_engine.submit(_signal(now, "A"), now=now)
    order_engine.submit(_signal(now, "A"), now=now)
    third_order_gate = order_engine.check_gates(_signal(now, "A"), now=now)

    loss_engine = PaperTradingEngine(
        PaperEngineConfig(
            max_daily_loss_fraction=engine_config.max_daily_loss_fraction,
            stop_after_consecutive_losses=(
                engine_config.stop_after_consecutive_losses
            ),
        ),
        equity=equity,
    )
    loss_engine.record_realized_pnl(
        -(equity * engine_config.max_daily_loss_fraction + 1.0),
        now=now,
    )
    daily_loss_gate = loss_engine.check_gates(_signal(now, "A"), now=now)

    consecutive_engine = PaperTradingEngine(
        PaperEngineConfig(
            stop_after_consecutive_losses=(
                engine_config.stop_after_consecutive_losses
            )
        ),
        equity=equity,
    )
    for _ in range(engine_config.stop_after_consecutive_losses or 0):
        consecutive_engine.record_realized_pnl(-1.0, now=now)
    consecutive_gate = consecutive_engine.check_gates(
        _signal(now, "A"),
        now=now,
    )

    empty_engine = PaperTradingEngine(engine_config, equity=equity)
    insufficient_gate = empty_engine.check_gates(
        _signal(now, "A", side=OrderSide.SELL),
        now=now,
    )

    checks = {
        "configured_position_count_fills_allowed": all(
            fill is not None for fill in position_fills
        ),
        "open_position_limit_blocks_fourth": (
            not fourth_gate.allowed
            and "open_position_limit" in fourth_gate.reasons
        ),
        "gross_exposure_limit_blocks_fourth": (
            "gross_exposure_limit" in fourth_gate.reasons
        ),
        "daily_order_limit_blocks_excess": (
            not third_order_gate.allowed
            and "daily_order_limit" in third_order_gate.reasons
        ),
        "daily_loss_limit_blocks_new_order": (
            not daily_loss_gate.allowed
            and "daily_loss_limit" in daily_loss_gate.reasons
        ),
        "consecutive_loss_limit_blocks_new_order": (
            not consecutive_gate.allowed
            and "consecutive_loss_limit" in consecutive_gate.reasons
        ),
        "sell_without_position_is_blocked": (
            not insufficient_gate.allowed
            and "insufficient_position" in insufficient_gate.reasons
        ),
    }
    passed = all(checks.values())
    return {
        "status": "PASS" if passed else "FAIL",
        "evidence_type": "PAPER_PORTFOLIO_RISK_LIMIT_VALIDATION",
        "validated_at_utc": now.isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        ),
        "checks": checks,
        "config": {
            "artifact": config_path,
            "sha256": sha256(path),
        },
        "implementation": {
            "artifact": "phoenix_core/trade/paper_engine.py",
            "sha256": sha256(ROOT / "phoenix_core/trade/paper_engine.py"),
        },
        "network_called": False,
        "broker_routes_called": False,
        "account_endpoints_called": False,
        "live_enabled": False,
        "production_changed": False,
        "paper_route_changed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/paper_trading.yaml")
    parser.add_argument(
        "--output",
        default=(
            "reports/paper_trading/risk/"
            "portfolio_risk_validation_latest.json"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = validate(args.config)
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    payload = {
        **result,
        "artifact": str(output.relative_to(ROOT)),
        "artifact_sha256": sha256(output),
    }
    print(
        json.dumps(payload, ensure_ascii=False)
        if args.json
        else f"{result['status']} {payload['artifact']}"
    )
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
