"""
phoenix_core/trade/trade_models.py
----------------------------------
Trade Simulation에서 사용하는 순수 데이터 모델.

주의:
- 이 파일은 계산 로직을 거의 갖지 않는다.
- pandas/yfinance/EngineRegistry에 의존하지 않는다.
- trade_rules.py, trade_sim_engine.py, benchmark.py가 공통으로 import해서 사용한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Dict, Optional


class TradeSide(str, Enum):
    LONG = "long"
    SHORT = "short"


class ExitReason(str, Enum):
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    TIME_EXIT = "time_exit"
    NO_DATA = "no_data"
    ERROR = "error"


class SameDayPolicy(str, Enum):
    """일봉에서 같은 날 TP/SL이 둘 다 닿았을 때 처리 정책."""

    STOP_FIRST = "stop_first"      # 보수적: 손절 먼저
    TAKE_PROFIT_FIRST = "tp_first" # 낙관적: 익절 먼저


@dataclass(frozen=True)
class TradeSimConfig:
    """단일 매매 시뮬레이션 설정."""

    side: TradeSide = TradeSide.LONG
    take_profit: float = 0.05
    stop_loss: float = 0.03
    hold_days: int = 5
    trailing_stop: Optional[float] = None
    same_day_policy: SameDayPolicy = SameDayPolicy.STOP_FIRST
    fee_bps: float = 0.0       # 1bp = 0.01%, 왕복이면 entry/exit 각각 적용
    slippage_bps: float = 0.0  # entry/exit 각각 적용

    def validate(self) -> None:
        if self.side != TradeSide.LONG:
            raise NotImplementedError("현재 Trade Simulator는 LONG만 지원합니다.")
        if not (0 < self.take_profit < 1):
            raise ValueError("take_profit은 0~1 사이 양수여야 합니다. 예: 0.05")
        if not (0 < self.stop_loss < 1):
            raise ValueError("stop_loss는 0~1 사이 양수여야 합니다. 예: 0.03")
        if self.hold_days <= 0:
            raise ValueError("hold_days는 1 이상이어야 합니다.")
        if self.trailing_stop is not None and not (0 < self.trailing_stop < 1):
            raise ValueError("trailing_stop은 None 또는 0~1 사이 양수여야 합니다.")
        if self.fee_bps < 0 or self.slippage_bps < 0:
            raise ValueError("fee_bps/slippage_bps는 음수일 수 없습니다.")


@dataclass
class TradeSignal:
    """벤치마크/랭킹 결과에서 Trade Simulator로 넘기는 진입 신호."""

    ticker: str
    as_of: date
    rank: int = 0
    suitability_score: float = 0.0
    confidence_score: float = 0.0
    risk_score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TradeResult:
    """단일 매매 시뮬레이션 결과."""

    ticker: str
    as_of: date
    entry_date: Optional[date]
    exit_date: Optional[date]
    entry_price: float
    exit_price: float
    gross_return: float
    net_return: float
    max_favorable_return: float
    max_adverse_return: float
    holding_days: int
    exit_reason: ExitReason
    hit_take_profit: bool = False
    hit_stop_loss: bool = False
    hit_trailing_stop: bool = False
    rank: int = 0
    suitability_score: float = 0.0
    confidence_score: float = 0.0
    risk_score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_win(self) -> bool:
        return self.net_return > 0

    @property
    def is_loss(self) -> bool:
        return self.net_return < 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "as_of": self.as_of.isoformat() if self.as_of else "",
            "entry_date": self.entry_date.isoformat() if self.entry_date else "",
            "exit_date": self.exit_date.isoformat() if self.exit_date else "",
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "gross_return": self.gross_return,
            "net_return": self.net_return,
            "max_favorable_return": self.max_favorable_return,
            "max_adverse_return": self.max_adverse_return,
            "holding_days": self.holding_days,
            "exit_reason": self.exit_reason.value,
            "hit_take_profit": int(self.hit_take_profit),
            "hit_stop_loss": int(self.hit_stop_loss),
            "hit_trailing_stop": int(self.hit_trailing_stop),
            "rank": self.rank,
            "suitability_score": self.suitability_score,
            "confidence_score": self.confidence_score,
            "risk_score": self.risk_score,
            **{f"meta_{k}": v for k, v in self.metadata.items() if isinstance(v, (str, int, float, bool))},
        }


@dataclass
class TradeSummary:
    """여러 TradeResult를 집계한 결과."""

    n_trades: int
    win_rate: float
    avg_return: float
    median_return: float
    cumulative_return: float
    max_drawdown: float
    profit_factor: float
    take_profit_rate: float
    stop_loss_rate: float
    trailing_stop_rate: float
    time_exit_rate: float
    avg_holding_days: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n_trades": self.n_trades,
            "win_rate": self.win_rate,
            "avg_return": self.avg_return,
            "median_return": self.median_return,
            "cumulative_return": self.cumulative_return,
            "max_drawdown": self.max_drawdown,
            "profit_factor": self.profit_factor,
            "take_profit_rate": self.take_profit_rate,
            "stop_loss_rate": self.stop_loss_rate,
            "trailing_stop_rate": self.trailing_stop_rate,
            "time_exit_rate": self.time_exit_rate,
            "avg_holding_days": self.avg_holding_days,
        }
