"""
trade_rules.py
---------------
Sprint 6-2

거래 규칙(TP/SL/Trailing/Time Exit)만 담당하는 모듈.
"""

from dataclasses import dataclass
from enum import Enum


class ExitReason(str, Enum):
    TAKE_PROFIT = "TP"
    STOP_LOSS = "SL"
    TRAILING = "TRAILING"
    TIME = "TIME"
    GAP = "GAP"
    HOLDING = "HOLDING"


@dataclass
class TradeRule:
    take_profit: float = 0.05
    stop_loss: float = 0.03
    trailing_stop: float | None = None
    max_hold_days: int = 5
    fee: float = 0.00015
    slippage: float = 0.0005
    stop_first_on_same_day: bool = True


def check_take_profit(entry_price: float, high_price: float, rule: TradeRule) -> bool:
    return high_price >= entry_price * (1 + rule.take_profit)


def check_stop_loss(entry_price: float, low_price: float, rule: TradeRule) -> bool:
    return low_price <= entry_price * (1 - rule.stop_loss)


def update_trailing_stop(highest_price: float, current_price: float, rule: TradeRule) -> bool:
    if rule.trailing_stop is None:
        return False
    trigger = highest_price * (1 - rule.trailing_stop)
    return current_price <= trigger


def apply_costs(raw_return: float, rule: TradeRule) -> float:
    """수수료+슬리피지 반영"""
    return raw_return - rule.fee - rule.slippage
