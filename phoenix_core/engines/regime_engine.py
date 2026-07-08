from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from ..interfaces import Engine
from ..models import MarketRegimeInput, MarketRegimeResult
from ..registry import EngineRegistry


def _asof_frame(df: pd.DataFrame, as_of) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    return df[df.index <= pd.Timestamp(as_of)].copy()


def _close(df: pd.DataFrame) -> pd.Series:
    return df.sort_index()["Close"].dropna()


def _ret(df: pd.DataFrame, days: int) -> float:
    c = _close(df)
    if len(c) <= days:
        return 0.0
    return float(c.iloc[-1] / c.iloc[-days - 1] - 1.0)


def _ma_gap(df: pd.DataFrame, days: int = 20) -> float:
    c = _close(df)
    if len(c) < days:
        return 0.0
    ma = c.rolling(days).mean().iloc[-1]
    return float(c.iloc[-1] / ma - 1.0) if ma else 0.0


def _latest(df: pd.DataFrame, default: float = 0.0) -> float:
    c = _close(df)
    return float(c.iloc[-1]) if len(c) else default


def _clip100(x: float) -> float:
    return float(np.clip(x, 0.0, 100.0))


@EngineRegistry.register("regime_engine", "regime_v1")
class MarketRegimeEngine(Engine[MarketRegimeInput, MarketRegimeResult]):
    """시장 국면 분류 엔진 v1.

    LLM 예측이 아니라 ETF/변동성 지표의 정량 규칙으로 현재 시장을 분류한다.
    v1은 규칙 기반이며, 향후 GMM/HMM으로 같은 인터페이스를 교체할 수 있다.
    """

    slot = "regime_engine"
    name = "regime_v1"

    def run(self, input_data: MarketRegimeInput) -> MarketRegimeResult:
        data: Dict[str, pd.DataFrame] = {
            ticker: _asof_frame(df, input_data.as_of)
            for ticker, df in input_data.market_ohlcv.items()
        }
        qqq_20 = _ret(data["QQQ"], 20) if "QQQ" in data else 0.0
        spy_20 = _ret(data["SPY"], 20) if "SPY" in data else 0.0
        iwm_20 = _ret(data["IWM"], 20) if "IWM" in data else 0.0
        smh_20 = _ret(data["SMH"], 20) if "SMH" in data else 0.0
        soxx_20 = _ret(data["SOXX"], 20) if "SOXX" in data else smh_20
        qqq_gap = _ma_gap(data["QQQ"], 20) if "QQQ" in data else 0.0
        spy_gap = _ma_gap(data["SPY"], 20) if "SPY" in data else 0.0
        vix = _latest(data["^VIX"], 20.0) if "^VIX" in data else 20.0
        semi_20 = max(smh_20, soxx_20)

        risk_score = _clip100((vix - 12.0) * 4.5 + max(-spy_20, 0) * 220 + max(-qqq_20, 0) * 220)
        momentum_score = _clip100(50 + (0.45 * qqq_20 + 0.35 * spy_20 + 0.20 * semi_20) * 800)
        breadth_proxy = _clip100(50 + (spy_20 - iwm_20) * -250 + (spy_gap + qqq_gap) * 200)
        volatility_score = _clip100(100 - (vix - 12.0) * 4.8)

        if vix >= 25 or (qqq_20 < -0.06 and spy_20 < -0.04):
            regime = "Risk Off"
        elif qqq_20 > 0.04 and semi_20 > 0.04 and qqq_20 > spy_20:
            regime = "AI Growth Rotation"
        elif spy_20 > 0.025 and qqq_20 > 0.02 and iwm_20 > 0.0:
            regime = "Broad Bull"
        elif spy_20 < -0.03 and qqq_20 < -0.03:
            regime = "Bear Trend"
        elif semi_20 > 0.03 and spy_20 <= 0.01:
            regime = "Narrow Tech Rotation"
        else:
            regime = "Neutral / Mixed"

        separation = max(abs(qqq_20), abs(spy_20), abs(semi_20), abs(iwm_20))
        confidence = _clip100(45 + separation * 650 + (10 if regime not in {"Neutral / Mixed"} else 0) + max(20 - vix, 0) * 0.8)
        components = {
            "qqq_20d_return": qqq_20,
            "spy_20d_return": spy_20,
            "iwm_20d_return": iwm_20,
            "semiconductor_20d_return": semi_20,
            "qqq_ma20_gap": qqq_gap,
            "spy_ma20_gap": spy_gap,
            "vix": vix,
            "momentum_score": momentum_score,
            "breadth_proxy": breadth_proxy,
            "volatility_score": volatility_score,
        }
        return MarketRegimeResult(
            as_of=input_data.as_of,
            regime=regime,
            confidence_score=confidence,
            risk_score=risk_score,
            momentum_score=momentum_score,
            breadth_score=breadth_proxy,
            volatility_score=volatility_score,
            components=components,
        )
