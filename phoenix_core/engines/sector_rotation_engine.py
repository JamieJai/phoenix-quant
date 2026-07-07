from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from ..interfaces import Engine
from ..models import SectorRotationInput, SectorRotationResult, SectorStrength
from ..registry import EngineRegistry


def _ret(df: pd.DataFrame, days: int) -> float:
    c = df.sort_index()["Close"].dropna()
    if len(c) <= days:
        return 0.0
    return float(c.iloc[-1] / c.iloc[-days - 1] - 1.0)


def _vol_adj_score(ret5: float, ret20: float) -> float:
    return float(np.clip(50 + ret5 * 550 + ret20 * 350, 0, 100))


@EngineRegistry.register("sector_rotation_engine", "rotation_v1")
class SectorRotationEngine(Engine[SectorRotationInput, SectorRotationResult]):
    """ETF 기반 섹터 로테이션 엔진 v1."""

    slot = "sector_rotation_engine"
    name = "rotation_v1"

    def run(self, input_data: SectorRotationInput) -> SectorRotationResult:
        data: Dict[str, pd.DataFrame] = input_data.market_ohlcv
        etfs: List[str] = input_data.sector_etfs or ["XLK", "XLY", "XLC", "XLF", "XLV", "XLI", "XLE", "XLP", "XLU", "XLB", "XLRE", "SMH", "SOXX", "QQQ"]
        strengths: list[SectorStrength] = []
        for etf in etfs:
            if etf not in data:
                continue
            r5 = _ret(data[etf], 5)
            r20 = _ret(data[etf], 20)
            score = _vol_adj_score(r5, r20)
            strengths.append(SectorStrength(etf=etf, score=score, return_5d=r5, return_20d=r20))
        strengths.sort(key=lambda x: x.score, reverse=True)
        for i, s in enumerate(strengths, start=1):
            s.rank = i

        target = None
        if input_data.target_sector_etf:
            for s in strengths:
                if s.etf == input_data.target_sector_etf:
                    target = s
                    break
        return SectorRotationResult(
            as_of=input_data.as_of,
            target_etf=input_data.target_sector_etf,
            target_strength=target,
            top_sectors=strengths[:10],
            all_strengths=strengths,
        )
