"""Bounded, research-only intraday strategy scoring.

This engine consumes point-in-time contracts and deliberately has no provider,
network, persistence, or production wiring.  Missing inputs reduce confidence;
they are never inferred from future observations.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, Sequence, Any
import math

from phoenix_core.intraday_data_contract import (
    IntradayMarketSnapshot, EventRiskSnapshot, KeyLevelSnapshot,
)


@dataclass
class IntradayStrategyResult:
    ticker: str
    timestamp: str
    opportunity_score: float
    confidence_score: float
    risk_score: float
    state: str
    momentum_acceleration_pct: Optional[float] = None
    relative_strength_pct: Optional[float] = None
    rvol_tod: Optional[float] = None
    vwap_distance_pct: Optional[float] = None
    chase_penalty: float = 0.0
    rr_ratio: Optional[float] = None
    warnings: list[str] = field(default_factory=list)
    features: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ret(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b in (None, 0):
        return None
    return (float(a) / float(b) - 1.0) * 100.0


def _num(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


class IntradayStrategyEngine:
    """Compute bounded scores from a single point-in-time snapshot."""

    def __init__(self, max_chase_penalty: float = 20.0, minimum_confidence: float = 50.0):
        self.max_chase_penalty = float(max_chase_penalty)
        self.minimum_confidence = float(minimum_confidence)

    def analyze(self, snapshot: IntradayMarketSnapshot,
                event: Optional[EventRiskSnapshot] = None,
                levels: Sequence[KeyLevelSnapshot] = ()) -> IntradayStrategyResult:
        p = _num(snapshot.current_price)
        prev = _num(snapshot.previous_close)
        op = _num(snapshot.day_open)
        vwap = _num(snapshot.vwap)
        atr = _num(snapshot.atr_intraday)
        bars = list(snapshot.bars or ())
        closes = [_num(b.close) for b in bars]
        closes = [x for x in closes if x is not None]
        r10 = _ret(p, closes[-3]) if len(closes) >= 3 else None
        r30 = _ret(p, closes[-7]) if len(closes) >= 7 else None
        accel = None if r10 is None or r30 is None else r10 - r30 / 3.0
        rs = None
        if snapshot.market_return_pct is not None and snapshot.sector_return_pct is not None:
            rs = (r10 or 0.0) - 0.5 * (float(snapshot.market_return_pct) + float(snapshot.sector_return_pct))
        vwap_dist = _ret(p, vwap)
        chase = 0.0
        if vwap_dist is not None and atr and p is not None and vwap:
            chase = max(0.0, abs(p-vwap) / atr - 0.5) * 8.0
        if vwap_dist is not None and vwap_dist > 8: chase += 4.0
        chase = min(self.max_chase_penalty, chase)
        opportunity = self._opportunity(snapshot, r10, r30, accel, rs, vwap_dist, chase, levels)
        confidence = self._confidence(snapshot, p, prev, r10, r30, vwap_dist)
        risk = self._risk(snapshot, event, chase, p, prev, atr)
        warnings: list[str] = []
        if confidence < self.minimum_confidence: warnings.append("데이터 신뢰도가 낮아 진입을 제한합니다.")
        if chase >= 8: warnings.append("VWAP/ATR 기준 추격 위험이 높습니다.")
        state = self._state(snapshot, opportunity, confidence, risk, r10, r30, accel, vwap_dist, chase)
        return IntradayStrategyResult(snapshot.ticker, snapshot.timestamp, opportunity, confidence, risk,
                                      state, accel, rs, snapshot.rvol_tod, vwap_dist, chase,
                                      features={"return_10m_pct": r10, "return_30m_pct": r30,
                                                "market_return_pct": snapshot.market_return_pct,
                                                "sector_return_pct": snapshot.sector_return_pct})

    evaluate = analyze

    def _opportunity(self, s, r10, r30, acc, rs, vd, chase, levels):
        vals = [50.0, 50.0, 50.0, 50.0, 50.0]
        vals[0] = 50 + min(50, max(-50, _num(s.market_return_pct) or 0) * 8)
        vals[1] = 50 + min(50, max(-50, _num(s.sector_return_pct) or 0) * 8)
        vals[2] = 50 + min(50, max(-50, (acc or 0) * 8 + (r10 or 0) * 3))
        vals[3] = 50 + min(50, max(-50, (rs or 0) * 5))
        vals[4] = 50 + min(50, max(-50, (vd or 0) * 4 + ((_num(s.rvol_tod) or 1)-1)*10)) - chase
        return round(max(0, min(100, sum(vals)/5)), 2)

    def _confidence(self, s, p, prev, r10, r30, vd):
        present = sum(x is not None for x in (p, prev, r10, r30, vd, s.rvol_tod, s.vwap))
        score = present / 7 * 70 + (20 if (s.freshness_seconds is not None and s.freshness_seconds <= 300) else 0)
        return round(max(0, min(100, score)), 2)

    def _risk(self, s, event, chase, p, prev, atr):
        risk = chase
        if atr and p: risk += min(25, atr/p*100*8)
        if event and event.earnings_days is not None: risk += {0:15, 1:10, 2:5}.get(event.earnings_days, 0)
        if prev and p and p < prev: risk += 8
        return round(max(0, min(100, risk)), 2)

    def _state(self, s, opp, conf, risk, r10, r30, acc, vd, chase):
        if conf < self.minimum_confidence or risk >= 75: return "NO_TRADE"
        if chase >= 10: return "OVEREXTENDED_CHASE"
        if r10 is not None and r30 is not None and acc > 0 and (vd or 0) > 0: return "MOMENTUM_LONG"
        if (vd or 0) < 0 and (r10 or 0) > 0: return "PULLBACK_LONG"
        return "NO_TRADE" if opp < 50 else "BREAKOUT_READY"
