from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass
class IntradayOverlayRankedItem:
    context: Any
    original_rank: int
    daily_rank_score: float
    intraday_component: float
    risk_penalty: float
    microstructure_adjustment: float
    adjusted_score: float


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except Exception:
        return default
    return v if math.isfinite(v) else default


def _feature(ctx: Any, name: str, default: float = 0.0) -> float:
    features = getattr(ctx, "features", {}) or {}
    return _finite(features.get(name), default)


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def score_intraday_overlay_context(ctx: Any, original_rank: int) -> IntradayOverlayRankedItem:
    daily_rank_score = max(0.0, 100.0 - (int(original_rank) - 1) * 12.0)
    intraday_score = _finite(getattr(ctx, "intraday_score", None), _feature(ctx, "intraday_score"))
    risk_score = _finite(getattr(ctx, "intraday_risk_score", None), _feature(ctx, "intraday_risk_score", 100.0))

    vwap_position = _feature(ctx, "vwap_position_pct")
    relative_volume = _feature(ctx, "relative_intraday_volume")
    pullback = _feature(ctx, "pullback_from_intraday_high_pct")
    gap = _feature(ctx, "gap_prev_close_pct")
    confidence = _feature(ctx, "data_confidence_score", 50.0)
    acceleration = _feature(ctx, "momentum_acceleration_pct")
    sector_rs = sum(_feature(ctx, name) for name in ("sector_rs_soxx_pct", "sector_rs_smh_pct", "sector_rs_qqq_pct")) / 3.0

    vwap_adjust = _clip(vwap_position * 2.0, -6.0, 6.0)
    volume_adjust = _clip((relative_volume - 1.0) * 4.0, 0.0, 8.0) if relative_volume > 1.0 else 0.0
    pullback_adjust = _clip((pullback + 1.0) * 1.5, -8.0, 3.0)
    chase_penalty = -8.0 if gap >= 8.0 else (-4.0 if gap >= 5.0 else 0.0)
    momentum_adjust = _clip(acceleration * 1.5, -5.0, 5.0)
    sector_adjust = _clip(sector_rs * 0.5, -5.0, 5.0)
    confidence_adjust = _clip((confidence - 70.0) * 0.05, -3.5, 1.5)
    microstructure_adjustment = vwap_adjust + volume_adjust + pullback_adjust + chase_penalty + momentum_adjust + sector_adjust + confidence_adjust

    intraday_component = 0.45 * intraday_score
    risk_penalty = 0.20 * risk_score
    adjusted_score = 0.45 * daily_rank_score + intraday_component - risk_penalty + microstructure_adjustment

    return IntradayOverlayRankedItem(
        context=ctx,
        original_rank=int(original_rank),
        daily_rank_score=float(daily_rank_score),
        intraday_component=float(intraday_component),
        risk_penalty=float(risk_penalty),
        microstructure_adjustment=float(microstructure_adjustment),
        adjusted_score=float(_clip(adjusted_score, 0.0, 100.0)),
    )


def rank_intraday_overlay_contexts(contexts: Iterable[Any], max_items: int | None = None) -> list[IntradayOverlayRankedItem]:
    ranked = [score_intraday_overlay_context(ctx, i) for i, ctx in enumerate(list(contexts), start=1)]
    ranked.sort(key=lambda item: (item.adjusted_score, -item.original_rank), reverse=True)
    if max_items is not None:
        return ranked[:max_items]
    return ranked
