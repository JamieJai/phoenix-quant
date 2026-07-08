from __future__ import annotations

from dataclasses import replace
from typing import Tuple

from .trade_models import EntryMode, ExitReason, SameDayRule, TradeConfig


def normalize_config(config: TradeConfig | None = None, **overrides) -> TradeConfig:
    """TradeConfig를 만들고 문자열 옵션을 Enum으로 정규화한다."""
    cfg = config or TradeConfig()
    if overrides:
        cfg = replace(cfg, **{k: v for k, v in overrides.items() if v is not None})

    if isinstance(cfg.entry_mode, str):
        cfg.entry_mode = EntryMode(cfg.entry_mode)
    if isinstance(cfg.same_day_rule, str):
        cfg.same_day_rule = SameDayRule(cfg.same_day_rule)
    return cfg


def take_profit_price(entry_price: float, config: TradeConfig) -> float:
    return float(entry_price) * (1.0 + float(config.take_profit))


def stop_loss_price(entry_price: float, config: TradeConfig) -> float:
    return float(entry_price) * (1.0 - float(config.stop_loss))


def trailing_stop_price(highest_price: float, config: TradeConfig) -> float | None:
    if config.trailing_stop is None:
        return None
    return float(highest_price) * (1.0 - float(config.trailing_stop))


def decide_intraday_exit(
    *,
    entry_price: float,
    day_open: float,
    day_high: float,
    day_low: float,
    highest_price: float,
    config: TradeConfig,
) -> Tuple[ExitReason | None, float | None]:
    """일봉 하나에서 TP/SL/Trailing 발생 여부를 판단한다.

    주의:
    일봉 데이터는 장중 순서를 알 수 없다. 같은 날 TP/SL이 모두 닿으면
    config.same_day_rule에 따라 보수적으로 판단한다.
    """

    tp_price = take_profit_price(entry_price, config)
    sl_price = stop_loss_price(entry_price, config)
    tr_price = trailing_stop_price(highest_price, config)

    hit_tp = day_high >= tp_price
    hit_sl = day_low <= sl_price
    hit_tr = tr_price is not None and day_low <= tr_price

    if hit_tp and (hit_sl or hit_tr):
        if config.same_day_rule == SameDayRule.TAKE_FIRST:
            return ExitReason.TAKE_PROFIT, tp_price
        if config.same_day_rule == SameDayRule.MIDPOINT:
            # 중립 가정: TP/SL 중간값으로 청산 처리
            adverse_price = sl_price if hit_sl else tr_price
            assert adverse_price is not None
            return ExitReason.TIME_EXIT, (tp_price + adverse_price) / 2.0
        # 기본: stop_first. 보수적 평가.
        if hit_sl:
            return ExitReason.STOP_LOSS, sl_price
        return ExitReason.TRAILING_STOP, tr_price

    if hit_sl:
        return ExitReason.STOP_LOSS, sl_price

    if hit_tr:
        return ExitReason.TRAILING_STOP, tr_price

    if hit_tp:
        return ExitReason.TAKE_PROFIT, tp_price

    return None, None


def apply_costs(gross_return: float, config: TradeConfig) -> float:
    return float(gross_return) - config.round_trip_cost()
