from __future__ import annotations

from typing import Callable, List, Optional

import numpy as np

from ..interfaces import BacktestEngine as BacktestEngineInterface
from ..models import BacktestResult, BacktestTrade, PatternRecord
from ..registry import EngineRegistry

DecisionFn = Callable[[PatternRecord], bool]


def _max_drawdown(cumulative_returns: np.ndarray) -> float:
    if len(cumulative_returns) == 0:
        return 0.0
    running_max = np.maximum.accumulate(cumulative_returns)
    drawdown = (running_max - cumulative_returns) / running_max
    return float(np.max(drawdown))


@EngineRegistry.register("backtest_engine", "as_of_v1")
class AsOfBacktestEngine(BacktestEngineInterface):
    name = "as_of_v1"

    def configure(self, **kwargs):
        self.actual_label_key = kwargs.get("actual_label_key", "hit_5pct_5d")
        self.return_key = kwargs.get("return_key", "fwd_max_ret_5d")
        return super().configure(**kwargs)

    def run(self, input_data):
        # input_data=(records, decision_fn)도 지원, 직접 인자 방식도 하위호환으로 지원하지 않음.
        records, decision_fn = input_data
        return self.evaluate(records, decision_fn)

    def evaluate(self, records: List[PatternRecord], decision_fn: DecisionFn,
                 actual_label_key: Optional[str] = None, return_key: Optional[str] = None) -> BacktestResult:
        actual_label_key = actual_label_key or self.actual_label_key
        return_key = return_key or self.return_key
        usable = []
        for r in records:
            if actual_label_key not in r.forward_labels or return_key not in r.forward_labels:
                continue
            if r.forward_labels[actual_label_key] is None or r.forward_labels[return_key] is None:
                continue
            if np.isnan(float(r.forward_labels[return_key])):
                continue
            usable.append(r)

        trades: List[BacktestTrade] = []
        for r in usable:
            predicted = bool(decision_fn(r))
            actual = bool(float(r.forward_labels[actual_label_key]) >= 0.5)
            trades.append(BacktestTrade(r.ticker, r.date, predicted, actual, float(r.forward_labels[return_key])))
        return self._compute_metrics(len(usable), trades)

    @staticmethod
    def _compute_metrics(usable_count: int, trades: List[BacktestTrade]) -> BacktestResult:
        taken = [t for t in trades if t.predicted_positive]
        if not taken:
            return BacktestResult(usable_count, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, trades)
        returns = np.array([t.forward_return for t in taken], dtype=float)
        hit_rate = float(np.mean([t.actual_positive for t in taken]))
        avg_return = float(np.mean(returns))
        max_return = float(np.max(returns))
        min_return = float(np.min(returns))
        sharpe = float(avg_return / returns.std()) if returns.std() > 1e-12 else 0.0
        ordered = sorted(taken, key=lambda t: t.as_of)
        cumulative = np.cumprod(1.0 + np.array([t.forward_return for t in ordered], dtype=float))
        mdd = _max_drawdown(cumulative)
        gains = returns[returns > 0].sum()
        losses = -returns[returns < 0].sum()
        profit_factor = float(gains / losses) if losses > 1e-12 else (999.0 if gains > 0 else 0.0)
        tp = sum(1 for t in trades if t.predicted_positive and t.actual_positive)
        fp = sum(1 for t in trades if t.predicted_positive and not t.actual_positive)
        fn = sum(1 for t in trades if not t.predicted_positive and t.actual_positive)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        return BacktestResult(usable_count, len(taken), hit_rate, avg_return, max_return, min_return,
                              sharpe, mdd, profit_factor, precision, recall, f1, trades)
