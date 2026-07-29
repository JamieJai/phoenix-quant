"""Durable, broker-free stateful shadow portfolio.

This module has no network or broker imports. Callers provide point-in-time
quotes and completed one-minute bars. SQLite transactions make signal
idempotency and restart recovery testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
from typing import Any, Iterable


@dataclass(frozen=True)
class ShadowQuote:
    ticker: str
    price: float
    available_at: datetime
    source: str = "YFINANCE_1M"


@dataclass(frozen=True)
class ShadowBar:
    ticker: str
    bar_start: datetime
    available_at: datetime
    open: float
    high: float
    low: float
    close: float
    source: str = "YFINANCE_1M"


@dataclass(frozen=True)
class ShadowContract:
    equity: float = 100_000.0
    risk_per_trade_fraction: float = 0.005
    max_notional_fraction: float = 0.20
    max_open_positions: int = 3
    max_open_positions_per_ticker: int = 1
    max_gross_exposure_fraction: float = 0.60
    max_daily_orders: int = 20
    max_daily_loss_fraction: float = 0.015
    stop_after_consecutive_losses: int = 3
    minimum_confidence: float = 70.0
    maximum_risk_score: float = 55.0
    maximum_context_age_seconds: int = 1800
    maximum_signal_delay_seconds: int = 600
    maximum_quote_age_seconds: int = 30
    stop_fraction: float = 0.01
    target_fraction: float = 0.02
    maximum_hold_minutes: int = 30
    commission_bps: float = 2.0
    slippage_bps: float = 5.0


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive timestamp")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def shadow_signal_id(row: dict[str, Any]) -> str:
    fields = [
        str(row.get("ticker", "")).upper().strip(),
        str(row.get("recorded_at", "")).strip(),
        str(row.get("timestamp", "")).strip(),
        str(row.get("source", "")).lower().strip(),
    ]
    return hashlib.sha256("\x1f".join(fields).encode()).hexdigest()


class ShadowLedger:
    def __init__(
        self,
        path: str | os.PathLike[str],
        contract: ShadowContract | None = None,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.contract = contract or ShadowContract()
        self._initialize()
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    signal_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    context_available_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL,
                    risk_score REAL,
                    status TEXT NOT NULL,
                    reason TEXT,
                    processed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS positions (
                    position_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    signal_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    stop_price REAL NOT NULL,
                    target_price REAL NOT NULL,
                    highest_price REAL NOT NULL,
                    lowest_price REAL NOT NULL,
                    closed_at TEXT,
                    exit_price REAL,
                    exit_reason TEXT,
                    gross_return REAL,
                    net_return REAL,
                    net_pnl REAL,
                    FOREIGN KEY(signal_id) REFERENCES signals(signal_id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_positions_status
                    ON positions(status, ticker);
                CREATE INDEX IF NOT EXISTS idx_signals_processed
                    ON signals(processed_at);
                """
            )

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if math.isfinite(result) else None

    @staticmethod
    def _timestamp(value: Any) -> datetime | None:
        try:
            result = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            return None
        return _utc(result) if result.tzinfo is not None else None

    def _event(
        self,
        connection: sqlite3.Connection,
        event_at: datetime,
        event_type: str,
        entity_id: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO events(event_at,event_type,entity_id,payload_json)
            VALUES(?,?,?,?)
            """,
            (
                _iso(event_at),
                event_type,
                entity_id,
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
            ),
        )

    def _risk_state(
        self,
        connection: sqlite3.Connection,
        now: datetime,
    ) -> dict[str, float | int]:
        day = _iso(now)[:10]
        open_row = connection.execute(
            """
            SELECT COUNT(*) AS count,
                   COALESCE(SUM(entry_price*quantity),0) AS gross
            FROM positions WHERE status='OPEN'
            """
        ).fetchone()
        order_count = connection.execute(
            """
            SELECT COUNT(*) AS count FROM signals
            WHERE status='OPENED' AND substr(processed_at,1,10)=?
            """,
            (day,),
        ).fetchone()["count"]
        daily_pnl = connection.execute(
            """
            SELECT COALESCE(SUM(net_pnl),0) AS pnl FROM positions
            WHERE status='CLOSED' AND substr(closed_at,1,10)=?
            """,
            (day,),
        ).fetchone()["pnl"]
        recent = connection.execute(
            """
            SELECT net_pnl FROM positions
            WHERE status='CLOSED' ORDER BY closed_at DESC, position_id DESC
            """
        ).fetchall()
        losses = 0
        for row in recent:
            if float(row["net_pnl"]) < 0:
                losses += 1
            else:
                break
        return {
            "open_positions": int(open_row["count"]),
            "gross_exposure": float(open_row["gross"]),
            "daily_orders": int(order_count),
            "daily_pnl": float(daily_pnl),
            "consecutive_losses": losses,
        }

    def process_signal(
        self,
        row: dict[str, Any],
        quote: ShadowQuote,
        *,
        now: datetime,
    ) -> dict[str, Any]:
        now = _utc(now)
        signal_id = shadow_signal_id(row)
        ticker = str(row.get("ticker", "")).upper().strip()
        recorded_at = self._timestamp(row.get("recorded_at"))
        context_at = self._timestamp(row.get("timestamp"))
        confidence = self._number(row.get("data_confidence_score"))
        risk_score = self._number(row.get("intraday_risk_score"))
        reasons: list[str] = []
        if not ticker:
            reasons.append("TICKER_MISSING")
        if str(row.get("source", "")).lower() != "yfinance":
            reasons.append("SIGNAL_SOURCE_INVALID")
        if recorded_at is None or context_at is None:
            reasons.append("SIGNAL_TIMESTAMP_INVALID")
        elif context_at > recorded_at:
            reasons.append("CONTEXT_NOT_AVAILABLE")
        else:
            context_age = (recorded_at - context_at).total_seconds()
            signal_delay = (now - recorded_at).total_seconds()
            if context_age > self.contract.maximum_context_age_seconds:
                reasons.append("CONTEXT_STALE")
            if signal_delay < 0:
                reasons.append("SIGNAL_FROM_FUTURE")
            elif signal_delay > self.contract.maximum_signal_delay_seconds:
                reasons.append("SIGNAL_PROCESSING_STALE")
        if confidence is None or confidence < self.contract.minimum_confidence:
            reasons.append("CONFIDENCE_GATE")
        if risk_score is None or risk_score > self.contract.maximum_risk_score:
            reasons.append("RISK_GATE")
        try:
            quote_at = _utc(quote.available_at)
        except ValueError:
            quote_at = now + timedelta(days=1)
            reasons.append("QUOTE_TIMESTAMP_INVALID")
        if quote.ticker.upper() != ticker or quote.source != "YFINANCE_1M":
            reasons.append("QUOTE_SOURCE_INVALID")
        quote_age = (now - quote_at).total_seconds()
        if quote_age < 0:
            reasons.append("QUOTE_FROM_FUTURE")
        elif quote_age > self.contract.maximum_quote_age_seconds:
            reasons.append("QUOTE_STALE")
        if not math.isfinite(quote.price) or quote.price <= 0:
            reasons.append("QUOTE_PRICE_INVALID")

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT status,reason FROM signals WHERE signal_id=?",
                (signal_id,),
            ).fetchone()
            if existing:
                return {
                    "status": "DUPLICATE",
                    "signal_id": signal_id,
                    "existing_status": existing["status"],
                    "reason": existing["reason"],
                }
            state = self._risk_state(connection, now)
            if state["open_positions"] >= self.contract.max_open_positions:
                reasons.append("OPEN_POSITION_LIMIT")
            ticker_positions = connection.execute(
                """
                SELECT COUNT(*) AS count FROM positions
                WHERE status='OPEN' AND ticker=?
                """,
                (ticker,),
            ).fetchone()["count"]
            if (
                int(ticker_positions)
                >= self.contract.max_open_positions_per_ticker
            ):
                reasons.append("SYMBOL_POSITION_LIMIT")
            if state["daily_orders"] >= self.contract.max_daily_orders:
                reasons.append("DAILY_ORDER_LIMIT")
            if state["daily_pnl"] <= -(
                self.contract.equity
                * self.contract.max_daily_loss_fraction
            ):
                reasons.append("DAILY_LOSS_LIMIT")
            if (
                state["consecutive_losses"]
                >= self.contract.stop_after_consecutive_losses
            ):
                reasons.append("CONSECUTIVE_LOSS_LIMIT")

            if reasons:
                reason = ",".join(sorted(set(reasons)))
                connection.execute(
                    """
                    INSERT INTO signals VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        signal_id,
                        ticker or "UNKNOWN",
                        _iso(recorded_at or now),
                        _iso(context_at or now),
                        str(row.get("source", "")),
                        confidence,
                        risk_score,
                        "REJECTED",
                        reason,
                        _iso(now),
                    ),
                )
                self._event(
                    connection,
                    now,
                    "SIGNAL_REJECTED",
                    signal_id,
                    {"reasons": sorted(set(reasons))},
                )
                return {
                    "status": "REJECTED",
                    "signal_id": signal_id,
                    "reasons": sorted(set(reasons)),
                }

            slip = self.contract.slippage_bps / 10_000.0
            entry = float(quote.price) * (1.0 + slip)
            stop = entry * (1.0 - self.contract.stop_fraction)
            target = entry * (1.0 + self.contract.target_fraction)
            risk_per_share = entry - stop
            risk_quantity = (
                self.contract.equity
                * self.contract.risk_per_trade_fraction
                / risk_per_share
            )
            notional_quantity = (
                self.contract.equity
                * self.contract.max_notional_fraction
                / entry
            )
            quantity = min(risk_quantity, notional_quantity)
            projected = float(state["gross_exposure"]) + entry * quantity
            if projected > (
                self.contract.equity
                * self.contract.max_gross_exposure_fraction
                + 1e-9
            ):
                reason = "GROSS_EXPOSURE_LIMIT"
                connection.execute(
                    """
                    INSERT INTO signals VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        signal_id,
                        ticker,
                        _iso(recorded_at),
                        _iso(context_at),
                        str(row.get("source", "")),
                        confidence,
                        risk_score,
                        "REJECTED",
                        reason,
                        _iso(now),
                    ),
                )
                self._event(
                    connection,
                    now,
                    "SIGNAL_REJECTED",
                    signal_id,
                    {"reasons": [reason]},
                )
                return {
                    "status": "REJECTED",
                    "signal_id": signal_id,
                    "reasons": [reason],
                }
            connection.execute(
                """
                INSERT INTO signals VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    signal_id,
                    ticker,
                    _iso(recorded_at),
                    _iso(context_at),
                    str(row.get("source", "")),
                    confidence,
                    risk_score,
                    "OPENED",
                    None,
                    _iso(now),
                ),
            )
            connection.execute(
                """
                INSERT INTO positions(
                    position_id,ticker,signal_id,status,opened_at,
                    entry_price,quantity,stop_price,target_price,
                    highest_price,lowest_price
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    signal_id,
                    ticker,
                    signal_id,
                    "OPEN",
                    _iso(now),
                    entry,
                    quantity,
                    stop,
                    target,
                    entry,
                    entry,
                ),
            )
            self._event(
                connection,
                now,
                "POSITION_OPENED",
                signal_id,
                {
                    "ticker": ticker,
                    "entry_price": entry,
                    "quantity": quantity,
                    "stop_price": stop,
                    "target_price": target,
                    "quote_available_at": _iso(quote_at),
                },
            )
            return {
                "status": "OPENED",
                "signal_id": signal_id,
                "ticker": ticker,
                "entry_price": entry,
                "quantity": quantity,
                "stop_price": stop,
                "target_price": target,
            }

    def process_bars(
        self,
        ticker: str,
        bars: Iterable[ShadowBar],
        *,
        now: datetime,
    ) -> list[dict[str, Any]]:
        now = _utc(now)
        ticker = ticker.upper()
        ordered = sorted(bars, key=lambda bar: _utc(bar.available_at))
        closed: list[dict[str, Any]] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            positions = connection.execute(
                """
                SELECT * FROM positions
                WHERE status='OPEN' AND ticker=? ORDER BY opened_at
                """,
                (ticker,),
            ).fetchall()
            for position in positions:
                opened_at = self._timestamp(position["opened_at"])
                if opened_at is None:
                    continue
                eligible = [
                    bar
                    for bar in ordered
                    if bar.ticker.upper() == ticker
                    and bar.source == "YFINANCE_1M"
                    and opened_at < _utc(bar.available_at) <= now
                ]
                highest = float(position["highest_price"])
                lowest = float(position["lowest_price"])
                exit_price = None
                exit_reason = None
                exit_at = None
                stop = float(position["stop_price"])
                target = float(position["target_price"])
                for bar in eligible:
                    highest = max(highest, float(bar.high))
                    lowest = min(lowest, float(bar.low))
                    hit_stop = float(bar.low) <= stop
                    hit_target = float(bar.high) >= target
                    if hit_stop:
                        exit_price = min(stop, float(bar.open))
                        exit_reason = (
                            "STOP_FIRST"
                            if hit_target
                            else "STOP_LOSS"
                        )
                    elif hit_target:
                        exit_price = target
                        exit_reason = "TAKE_PROFIT"
                    elif _utc(bar.available_at) >= opened_at + timedelta(
                        minutes=self.contract.maximum_hold_minutes
                    ):
                        exit_price = float(bar.close)
                        exit_reason = "TIME_EXIT"
                    if exit_reason:
                        exit_at = _utc(bar.available_at)
                        break
                connection.execute(
                    """
                    UPDATE positions
                    SET highest_price=?,lowest_price=?
                    WHERE position_id=?
                    """,
                    (highest, lowest, position["position_id"]),
                )
                if exit_reason is None or exit_price is None or exit_at is None:
                    continue
                exit_fill = exit_price * (
                    1.0 - self.contract.slippage_bps / 10_000.0
                )
                entry = float(position["entry_price"])
                quantity = float(position["quantity"])
                gross_return = exit_fill / entry - 1.0
                fee_rate = self.contract.commission_bps / 10_000.0
                entry_fee = entry * quantity * fee_rate
                exit_fee = exit_fill * quantity * fee_rate
                net_pnl = (
                    (exit_fill - entry) * quantity
                    - entry_fee
                    - exit_fee
                )
                net_return = net_pnl / (entry * quantity)
                connection.execute(
                    """
                    UPDATE positions SET
                        status='CLOSED',closed_at=?,exit_price=?,
                        exit_reason=?,gross_return=?,net_return=?,net_pnl=?
                    WHERE position_id=?
                    """,
                    (
                        _iso(exit_at),
                        exit_fill,
                        exit_reason,
                        gross_return,
                        net_return,
                        net_pnl,
                        position["position_id"],
                    ),
                )
                payload = {
                    "status": "CLOSED",
                    "position_id": position["position_id"],
                    "ticker": ticker,
                    "closed_at": _iso(exit_at),
                    "exit_price": exit_fill,
                    "exit_reason": exit_reason,
                    "gross_return": gross_return,
                    "net_return": net_return,
                    "net_pnl": net_pnl,
                }
                self._event(
                    connection,
                    exit_at,
                    "POSITION_CLOSED",
                    position["position_id"],
                    payload,
                )
                closed.append(payload)
        return closed

    def snapshot(self) -> dict[str, Any]:
        with self._connect() as connection:
            signals = {
                row["status"]: int(row["count"])
                for row in connection.execute(
                    "SELECT status,COUNT(*) AS count FROM signals GROUP BY status"
                )
            }
            positions = {
                row["status"]: int(row["count"])
                for row in connection.execute(
                    "SELECT status,COUNT(*) AS count FROM positions GROUP BY status"
                )
            }
            pnl = connection.execute(
                """
                SELECT COUNT(*) AS count,
                       COALESCE(SUM(net_pnl),0) AS total,
                       AVG(net_return) AS mean_return
                FROM positions WHERE status='CLOSED'
                """
            ).fetchone()
            closed_rows = connection.execute(
                """
                SELECT net_return,net_pnl FROM positions
                WHERE status='CLOSED'
                ORDER BY closed_at,position_id
                """
            ).fetchall()
            returns = [float(row["net_return"]) for row in closed_rows]
            equity = self.contract.equity
            peak = equity
            maximum_drawdown = 0.0
            for row in closed_rows:
                equity += float(row["net_pnl"])
                peak = max(peak, equity)
                if peak > 0:
                    maximum_drawdown = max(
                        maximum_drawdown,
                        (peak - equity) / peak,
                    )
            signal_rows = connection.execute(
                "SELECT status,reason FROM signals"
            ).fetchall()
            quote_rejections = sum(
                row["status"] == "REJECTED"
                and "SIGNAL_PROCESSING_STALE"
                not in str(row["reason"] or "")
                and any(
                    reason in str(row["reason"] or "")
                    for reason in (
                        "QUOTE_STALE",
                        "QUOTE_FROM_FUTURE",
                        "QUOTE_PRICE_INVALID",
                        "QUOTE_SOURCE_INVALID",
                        "QUOTE_TIMESTAMP_INVALID",
                    )
                )
                for row in signal_rows
            )
            signal_total = len(signal_rows)
            events = connection.execute(
                "SELECT COUNT(*) AS count FROM events"
            ).fetchone()["count"]
            span = connection.execute(
                """
                SELECT MIN(processed_at) AS first_at,
                       MAX(processed_at) AS last_at
                FROM signals
                """
            ).fetchone()
            first_at = self._timestamp(span["first_at"])
            last_at = self._timestamp(span["last_at"])
            observed_days = (
                (last_at - first_at).total_seconds() / 86400.0
                if first_at is not None and last_at is not None
                else 0.0
            )
        return {
            "signals": signals,
            "positions": positions,
            "closed_positions": int(pnl["count"]),
            "total_net_pnl": float(pnl["total"]),
            "mean_net_return": (
                float(pnl["mean_return"])
                if pnl["mean_return"] is not None
                else None
            ),
            "win_rate": (
                sum(value > 0 for value in returns) / len(returns)
                if returns
                else None
            ),
            "maximum_drawdown_fraction": maximum_drawdown,
            "execution_quote_rejections": quote_rejections,
            "execution_quote_rejection_rate": (
                quote_rejections / signal_total if signal_total else None
            ),
            "first_signal_processed_at": (
                _iso(first_at) if first_at is not None else None
            ),
            "last_signal_processed_at": (
                _iso(last_at) if last_at is not None else None
            ),
            "observed_days": observed_days,
            "event_count": int(events),
            "broker_routes_called": False,
            "account_endpoints_called": False,
            "live_enabled": False,
        }

    def processed_signal_ids(self) -> set[str]:
        with self._connect() as connection:
            return {
                str(row["signal_id"])
                for row in connection.execute(
                    "SELECT signal_id FROM signals"
                )
            }

    def open_tickers(self) -> list[str]:
        with self._connect() as connection:
            return [
                str(row["ticker"])
                for row in connection.execute(
                    """
                    SELECT DISTINCT ticker FROM positions
                    WHERE status='OPEN' ORDER BY ticker
                    """
                )
            ]
