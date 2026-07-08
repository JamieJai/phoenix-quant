from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from ..interfaces import ContextEngine as ContextEngineInterface
from ..models import ContextEngineInput, MarketContext
from ..registry import EngineRegistry


def _asof_frame(df: pd.DataFrame, as_of) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    return df[df.index <= pd.Timestamp(as_of)].copy()


def _trend(df: pd.DataFrame, window: int = 20) -> float:
    d = df.sort_index().dropna(subset=["Close"])
    if len(d) < window + 1:
        return 0.0
    close = d["Close"]
    ma = close.rolling(window).mean()
    return float(((close.iloc[-1] - ma.iloc[-1]) / ma.iloc[-1]) if ma.iloc[-1] else 0.0)


def _latest_close(df: pd.DataFrame) -> float:
    d = df.sort_index().dropna(subset=["Close"])
    return float(d["Close"].iloc[-1]) if len(d) else 0.0


def _clip100(x: float) -> float:
    return float(np.clip(x, 0.0, 100.0))


@EngineRegistry.register("context_engine", "market_v1")
class MarketContextEngine(ContextEngineInterface):
    name = "market_v1"

    def run(self, input_data: ContextEngineInput) -> MarketContext:
        data: Dict[str, pd.DataFrame] = {
            ticker: _asof_frame(df, input_data.as_of)
            for ticker, df in input_data.market_ohlcv.items()
        }
        spy_t = _trend(data["SPY"]) if "SPY" in data else 0.0
        qqq_t = _trend(data["QQQ"]) if "QQQ" in data else 0.0
        sector_t = _trend(data[input_data.sector_etf]) if input_data.sector_etf and input_data.sector_etf in data else qqq_t
        vix = _latest_close(data["^VIX"]) if "^VIX" in data else 20.0

        def trend_score(t: float) -> float:
            return _clip100(50.0 + t * 500.0)

        vix_score = _clip100(100.0 * (30.0 - np.clip(vix, 13.0, 30.0)) / (30.0 - 13.0))
        market_score = _clip100(0.25 * trend_score(spy_t) + 0.25 * trend_score(qqq_t) +
                                0.25 * trend_score(sector_t) + 0.25 * vix_score)
        if vix >= 25:
            regime, risk = "high_vol", "High"
        elif spy_t > 0.02 and qqq_t > 0.02:
            regime, risk = "bull", "Low"
        elif spy_t < -0.02 and qqq_t < -0.02:
            regime, risk = "bear", "High"
        else:
            regime, risk = "neutral", "Medium"
        return MarketContext(
            as_of=input_data.as_of,
            regime=regime,
            risk_level=risk,
            market_score=market_score,
            trend_scores={"SPY": spy_t, "QQQ": qqq_t, "SECTOR": sector_t, "VIX_SCORE": vix_score},
            vix_level=vix,
            raw={"sector_etf": input_data.sector_etf},
        )
