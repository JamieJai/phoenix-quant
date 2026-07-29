"""Research-only paper trading engine.

This module deliberately has no broker/network integration.  It turns a validated
signal into a simulated fill only when risk gates pass, and keeps an in-memory
audit trail suitable for later persistence by a caller.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import json
import math


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class PaperSignal:
    symbol: str
    side: OrderSide = OrderSide.BUY
    price: float = 0.0
    quantity: float = 0.0
    confidence: float = 0.0
    rr_ratio: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    data_timestamp: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PaperOrder:
    order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    signal_price: float
    submitted_at: datetime


@dataclass(frozen=True)
class PaperFill:
    order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    fee: float
    slippage: float
    filled_at: datetime


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    reasons: List[str] = field(default_factory=list)


@dataclass
class PaperEngineConfig:
    min_confidence: float = 50.0
    min_rr_ratio: float = 1.2
    max_loss_per_trade: float = 0.01  # fraction of equity
    fee_bps: float = 1.5
    slippage_bps: float = 5.0
    max_data_age_seconds: int = 300
    max_position_value: Optional[float] = None
    max_open_positions: Optional[int] = None
    max_gross_exposure_fraction: Optional[float] = None
    max_orders_per_day: Optional[int] = None
    max_daily_loss_fraction: Optional[float] = None
    stop_after_consecutive_losses: Optional[int] = None


class PaperTradingEngine:
    """Risk-gated, deterministic paper execution (never sends live orders)."""

    def __init__(self, config: Optional[PaperEngineConfig] = None, *, equity: float = 100_000.0):
        self.config = config or PaperEngineConfig()
        self.equity = float(equity)
        self.kill_switch = False
        self.audit_log: List[Dict[str, Any]] = []
        self._counter = 0
        self._positions: Dict[str, Dict[str, float]] = {}
        self._orders_by_day: Dict[str, int] = {}
        self._realized_pnl_by_day: Dict[str, float] = {}
        self._consecutive_losses = 0

    def set_kill_switch(self, enabled: bool = True) -> None:
        self.kill_switch = bool(enabled)
        self._audit("kill_switch", enabled=self.kill_switch)

    def check_gates(self, signal: PaperSignal, *, now: Optional[datetime] = None) -> GateResult:
        cfg = self.config
        now = now or datetime.now(timezone.utc)
        reasons: List[str] = []
        if self.kill_switch:
            reasons.append("kill_switch")
        if not signal.symbol or signal.price <= 0 or signal.quantity <= 0:
            reasons.append("invalid_order")
        if not math.isfinite(signal.confidence) or signal.confidence < cfg.min_confidence:
            reasons.append("confidence_below_minimum")
        if not math.isfinite(signal.rr_ratio) or signal.rr_ratio < cfg.min_rr_ratio:
            reasons.append("rr_below_minimum")
        if signal.data_timestamp is None:
            reasons.append("missing_data_timestamp")
        else:
            age = (now.astimezone(timezone.utc) - signal.data_timestamp.astimezone(timezone.utc)).total_seconds()
            if age < 0 or age > cfg.max_data_age_seconds:
                reasons.append("stale_data")
        value = signal.price * signal.quantity
        if cfg.max_position_value is not None and value > cfg.max_position_value:
            reasons.append("position_limit")
        day = now.astimezone(timezone.utc).date().isoformat()
        if (
            cfg.max_orders_per_day is not None
            and self._orders_by_day.get(day, 0) >= cfg.max_orders_per_day
        ):
            reasons.append("daily_order_limit")
        if (
            cfg.max_daily_loss_fraction is not None
            and self._realized_pnl_by_day.get(day, 0.0)
            <= -(self.equity * cfg.max_daily_loss_fraction)
        ):
            reasons.append("daily_loss_limit")
        if (
            cfg.stop_after_consecutive_losses is not None
            and self._consecutive_losses >= cfg.stop_after_consecutive_losses
        ):
            reasons.append("consecutive_loss_limit")
        position = self._positions.get(signal.symbol, {})
        current_quantity = float(position.get("quantity", 0.0))
        if signal.side == OrderSide.BUY:
            if (
                current_quantity <= 0
                and cfg.max_open_positions is not None
                and self.open_position_count >= cfg.max_open_positions
            ):
                reasons.append("open_position_limit")
            if cfg.max_gross_exposure_fraction is not None:
                projected = self.gross_exposure + value
                if projected > self.equity * cfg.max_gross_exposure_fraction + 1e-9:
                    reasons.append("gross_exposure_limit")
        elif current_quantity + 1e-12 < signal.quantity:
            reasons.append("insufficient_position")
        # A signal's worst-case risk must not exceed configured equity risk.
        requested_loss = float(signal.metadata.get("risk_fraction", cfg.max_loss_per_trade))
        if requested_loss > cfg.max_loss_per_trade:
            reasons.append("max_loss_exceeded")
        return GateResult(not reasons, reasons)

    def submit(self, signal: PaperSignal, *, now: Optional[datetime] = None) -> Optional[PaperFill]:
        now = now or datetime.now(timezone.utc)
        gate = self.check_gates(signal, now=now)
        self._audit("gate", symbol=signal.symbol, allowed=gate.allowed, reasons=gate.reasons)
        if not gate.allowed:
            return None
        self._counter += 1
        order = PaperOrder(f"paper-{self._counter:08d}", signal.symbol, signal.side, signal.quantity, signal.price, now)
        slip = self.config.slippage_bps / 10_000.0
        # Adverse slippage: buy higher, sell lower.
        fill_price = signal.price * (1.0 + slip if signal.side == OrderSide.BUY else 1.0 - slip)
        fee = fill_price * signal.quantity * self.config.fee_bps / 10_000.0
        fill = PaperFill(order.order_id, order.symbol, order.side, order.quantity, fill_price, fee,
                         abs(fill_price - signal.price) * signal.quantity, now)
        self._audit("order", **asdict(order))
        self._audit("fill", **asdict(fill))
        day = now.astimezone(timezone.utc).date().isoformat()
        self._orders_by_day[day] = self._orders_by_day.get(day, 0) + 1
        self._apply_fill(fill, day=day)
        return fill

    @property
    def open_position_count(self) -> int:
        return sum(
            float(position.get("quantity", 0.0)) > 1e-12
            for position in self._positions.values()
        )

    @property
    def gross_exposure(self) -> float:
        return sum(
            abs(float(position.get("quantity", 0.0)))
            * float(position.get("mark_price", position.get("average_price", 0.0)))
            for position in self._positions.values()
        )

    def record_realized_pnl(
        self,
        amount: float,
        *,
        now: Optional[datetime] = None,
        source: str = "external_paper_outcome",
    ) -> None:
        now = now or datetime.now(timezone.utc)
        day = now.astimezone(timezone.utc).date().isoformat()
        value = float(amount)
        self._realized_pnl_by_day[day] = (
            self._realized_pnl_by_day.get(day, 0.0) + value
        )
        if value < 0:
            self._consecutive_losses += 1
        elif value > 0:
            self._consecutive_losses = 0
        self._audit(
            "realized_pnl",
            amount=value,
            day=day,
            cumulative=self._realized_pnl_by_day[day],
            consecutive_losses=self._consecutive_losses,
            source=source,
        )

    def _apply_fill(self, fill: PaperFill, *, day: str) -> None:
        position = self._positions.setdefault(
            fill.symbol,
            {"quantity": 0.0, "average_price": 0.0, "mark_price": fill.price},
        )
        quantity = float(position["quantity"])
        average = float(position["average_price"])
        if fill.side == OrderSide.BUY:
            new_quantity = quantity + fill.quantity
            position["average_price"] = (
                (quantity * average + fill.quantity * fill.price) / new_quantity
            )
            position["quantity"] = new_quantity
            position["mark_price"] = fill.price
            return
        closed = min(quantity, fill.quantity)
        realized = (fill.price - average) * closed - fill.fee
        position["quantity"] = max(0.0, quantity - closed)
        position["mark_price"] = fill.price
        if position["quantity"] <= 1e-12:
            position["average_price"] = 0.0
        self.record_realized_pnl(
            realized,
            now=datetime.fromisoformat(f"{day}T00:00:00+00:00"),
            source="paper_sell_fill",
        )

    def _audit(self, event: str, **payload: Any) -> None:
        self.audit_log.append({"event": event, "timestamp": datetime.now(timezone.utc).isoformat(), **payload})

    def audit_json(self) -> str:
        return json.dumps(self.audit_log, default=str, ensure_ascii=False)
