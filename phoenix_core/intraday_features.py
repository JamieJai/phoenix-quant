from __future__ import annotations

import math
from typing import Optional

INTRADAY_FEATURE_NAMES = [
    "gap_prev_close_pct",
    "session_return_pct",
    "ret_fast_3bar_pct",
    "ret_slow_2bar_pct",
    "relative_intraday_volume",
    "vwap_position_pct",
    "pullback_from_intraday_high_pct",
    "intraday_score",
    "intraday_risk_score",
]


def _clean_float(value: Optional[float]) -> float:
    if value is None:
        return math.nan
    try:
        v = float(value)
    except Exception:
        return math.nan
    return v if math.isfinite(v) else math.nan


def build_intraday_feature_dict(
    *,
    gap_prev_close_pct: Optional[float],
    session_return_pct: Optional[float],
    ret_fast_3bar_pct: Optional[float],
    ret_slow_2bar_pct: Optional[float],
    relative_intraday_volume: Optional[float],
    vwap_position_pct: Optional[float],
    pullback_from_intraday_high_pct: Optional[float],
    intraday_score: Optional[float],
    intraday_risk_score: Optional[float],
) -> dict[str, float]:
    values = {
        "gap_prev_close_pct": gap_prev_close_pct,
        "session_return_pct": session_return_pct,
        "ret_fast_3bar_pct": ret_fast_3bar_pct,
        "ret_slow_2bar_pct": ret_slow_2bar_pct,
        "relative_intraday_volume": relative_intraday_volume,
        "vwap_position_pct": vwap_position_pct,
        "pullback_from_intraday_high_pct": pullback_from_intraday_high_pct,
        "intraday_score": intraday_score,
        "intraday_risk_score": intraday_risk_score,
    }
    return {name: _clean_float(values.get(name)) for name in INTRADAY_FEATURE_NAMES}
