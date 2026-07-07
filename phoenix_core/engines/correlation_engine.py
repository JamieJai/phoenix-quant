from __future__ import annotations

from typing import Dict, List

import pandas as pd

from ..interfaces import Engine
from ..models import CorrelationInput, CorrelationResult
from ..registry import EngineRegistry


def _ret_series(df: pd.DataFrame) -> pd.Series:
    return df.sort_index()["Close"].dropna().pct_change().dropna()


@EngineRegistry.register("correlation_engine", "correlation_v1")
class CorrelationEngine(Engine[CorrelationInput, CorrelationResult]):
    """티커와 주요 지수/동종 대형주 간 수익률 상관관계 계산."""

    slot = "correlation_engine"
    name = "correlation_v1"

    def run(self, input_data: CorrelationInput) -> CorrelationResult:
        data: Dict[str, pd.DataFrame] = input_data.ohlcv
        ticker = input_data.ticker.upper()
        if ticker not in data:
            return CorrelationResult(ticker=ticker, as_of=input_data.as_of, correlations={})
        base = _ret_series(data[ticker])
        comps: List[str] = list(dict.fromkeys(input_data.compare_tickers))
        result: dict[str, dict[str, float]] = {}
        for comp in comps:
            if comp == ticker or comp not in data:
                continue
            other = _ret_series(data[comp])
            joined = pd.concat([base.rename("base"), other.rename("other")], axis=1).dropna()
            if len(joined) < 30:
                continue
            per = {}
            for win in input_data.windows:
                tail = joined.tail(win)
                if len(tail) >= min(30, win):
                    per[f"corr_{win}d"] = float(tail["base"].corr(tail["other"]))
            if per:
                result[comp] = per
        return CorrelationResult(ticker=ticker, as_of=input_data.as_of, correlations=result)
