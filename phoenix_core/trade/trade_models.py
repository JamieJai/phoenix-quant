from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Dict, List, Optional


class EntryMode(str, Enum):
    """진입 가격 기준."""

    CLOSE = "close"
    NEXT_OPEN = "next_open"


class ExitReason(str, Enum):
    TAKE_PROFIT = "TP"
    STOP_LOSS = "SL"
    TRAILING_STOP = "TRAILING"
    TIME_EXIT = "TIME"
    NO_DATA = "NO_DATA"


class SameDayRule(str, Enum):
    """같은 일봉에서 TP/SL이 동시에 닿았을 때의 보수성 규칙."""

    STOP_FIRST = "stop_first"
    TAKE_FIRST = "take_first"
    MIDPOINT = "midpoint"


@dataclass
class TradeConfig:
    take_profit: float = 0.05
    stop_loss: float = 0.03
    max_hold_days: int = 5
    trailing_stop: Optional[float] = None
    entry_mode: EntryMode = EntryMode.CLOSE
    same_day_rule: SameDayRule = SameDayRule.STOP_FIRST
    fee_bps: float = 1.5
    slippage_bps: float = 5.0

    def round_trip_cost(self) -> float:
        """왕복 비용. bps 기준 fee/slippage를 수익률 차감값으로 변환."""
        return 2.0 * (self.fee_bps + self.slippage_bps) / 10000.0


@dataclass
class TradeCandidate:
    ticker: str
    as_of: date
    score: float = 0.0
    rank: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TradeResult:
    ticker: str
    as_of: date
    entry_date: Optional[date]
    exit_date: Optional[date]
    entry_price: float
    exit_price: float
    gross_return: float
    net_return: float
    hold_days: int
    exit_reason: ExitReason
    max_high_return: float
    max_low_return: float
    hit_take_profit: bool = False
    hit_stop_loss: bool = False
    hit_trailing_stop: bool = False
    score: float = 0.0
    rank: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_win(self) -> bool:
        return self.net_return > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "as_of": self.as_of,
            "entry_date": self.entry_date,
            "exit_date": self.exit_date,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "gross_return": self.gross_return,
            "net_return": self.net_return,
            "hold_days": self.hold_days,
            "exit_reason": self.exit_reason.value,
            "max_high_return": self.max_high_return,
            "max_low_return": self.max_low_return,
            "hit_take_profit": self.hit_take_profit,
            "hit_stop_loss": self.hit_stop_loss,
            "hit_trailing_stop": self.hit_trailing_stop,
            "score": self.score,
            "rank": self.rank,
            **self.metadata,
        }


@dataclass
class TradeSummary:
    n_trades: int
    win_rate: float
    avg_return: float
    median_return: float
    cumulative_return: float
    mdd: float
    profit_factor: float
    tp_rate: float
    sl_rate: float
    trailing_rate: float
    time_exit_rate: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n_trades": self.n_trades,
            "win_rate": self.win_rate,
            "avg_return": self.avg_return,
            "median_return": self.median_return,
            "cumulative_return": self.cumulative_return,
            "mdd": self.mdd,
            "profit_factor": self.profit_factor,
            "tp_rate": self.tp_rate,
            "sl_rate": self.sl_rate,
            "trailing_rate": self.trailing_rate,
            "time_exit_rate": self.time_exit_rate,
        }
