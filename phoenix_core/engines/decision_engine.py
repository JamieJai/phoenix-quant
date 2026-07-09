from __future__ import annotations

import numpy as np

from ..interfaces import DecisionEngine as DecisionEngineInterface
from ..models import DecisionInput, DecisionResult
from ..registry import EngineRegistry


def _clip100(x: float) -> float:
    return float(np.clip(x, 0.0, 100.0))


def _label(score: float) -> str:
    # 참고용 4단계 라벨입니다. 관심=우선 관찰, 관찰=추가 확인, 보류=매매 후보 해석 금지, 제외=제외 대상.
    if score >= 60:
        return "관심"
    if score >= 45:
        return "관찰"
    if score >= 30:
        return "보류"
    return "제외"


@EngineRegistry.register("decision_engine", "weighted_v1")
class WeightedDecisionEngine(DecisionEngineInterface):
    """MVP 의사결정 엔진 v1.1.

    v1.0과 같은 핵심 점수 체계를 유지하되, 최종 점수와 신뢰도가 왜 그렇게
    계산됐는지 설명 가능한 breakdown을 추가한다.
    """

    name = "weighted_v1"

    def configure(self, **kwargs):
        self.min_trades_for_confidence = kwargs.get("min_trades_for_confidence", 20)
        return super().configure(**kwargs)

    def run(self, input_data: DecisionInput) -> DecisionResult:
        anomaly = float(input_data.pattern.anomaly_percentile)
        hit5 = float(input_data.similarity.hit_rate_5d)
        hit10 = float(input_data.similarity.hit_rate_10d)
        market_score = float(input_data.market_context.market_score)
        sector_trend = float(input_data.market_context.trend_scores.get("SECTOR", 0.0))
        sector_rotation_score = None
        if input_data.sector_rotation and input_data.sector_rotation.target_strength:
            sector_rotation_score = float(input_data.sector_rotation.target_strength.score)
        vol = float(input_data.feature_vector.values.get("volatility_20d", 0.03))

        # 1) 핵심 서브 점수
        surge_score = _clip100(0.35 * anomaly + 0.65 * hit5 * 100.0)
        stability = _clip100(100.0 * (1.0 - min(vol / 0.06, 1.0)))
        hold_score = _clip100(0.60 * hit10 * 100.0 + 0.40 * stability)

        # 섹터 20일 추세를 -10~+10점 보정으로 제한한다.
        sector_adjust = float(np.clip(sector_trend * 250.0, -10.0, 10.0))
        if sector_rotation_score is not None:
            sector_adjust += float(np.clip((sector_rotation_score - 50.0) * 0.18, -8.0, 8.0))

        # 위험 페널티: 변동성 과열 및 VIX/시장 약세를 작게 반영한다.
        volatility_penalty = float(np.clip((vol / 0.06) * 10.0, 0.0, 10.0))
        vix = float(input_data.market_context.vix_level)
        vix_penalty = 0.0
        if vix >= 30:
            vix_penalty = 10.0
        elif vix >= 25:
            vix_penalty = 7.0
        elif vix >= 20:
            vix_penalty = 4.0

        regime_bonus = 0.0
        if input_data.regime_result:
            if input_data.regime_result.regime in {"AI Growth Rotation", "Broad Bull", "Narrow Tech Rotation"}:
                regime_bonus = 5.0
            elif input_data.regime_result.regime in {"Risk Off", "Bear Trend"}:
                regime_bonus = -8.0

        # 2) 최종 점수 기여도. 합계가 suitability_score가 되도록 구성한다.
        score_breakdown = {
            "pattern_contribution": 0.42 * surge_score,
            "hold_contribution": 0.23 * hold_score,
            "market_contribution": 0.25 * market_score,
            "sector_adjustment": sector_adjust,
            "regime_adjustment": regime_bonus,
            "risk_penalty": -(volatility_penalty + vix_penalty),
        }
        suitability_raw = sum(score_breakdown.values())
        suitability = _clip100(suitability_raw)

        # 3) 신뢰도. 유사 사례 수, 평균 유사도, 시장 명확성, 데이터 안정성의 합산.
        n = int(getattr(input_data.similarity, "n_unique_dates", 0) or input_data.similarity.n_similar)
        n_component = _clip100(100.0 * min(n / self.min_trades_for_confidence, 1.0))
        sim_component = _clip100(input_data.similarity.avg_similarity * 100.0)
        regime_component = 75.0 if input_data.market_context.regime in {"bull", "neutral"} else 45.0
        if input_data.regime_result:
            regime_component = max(regime_component, input_data.regime_result.confidence_score)
        data_component = 85.0 if np.isfinite(vol) else 30.0
        confidence_breakdown = {
            "similar_case_count": 0.35 * n_component,
            "average_similarity": 0.35 * sim_component,
            "market_regime_clarity": 0.15 * regime_component,
            "data_quality": 0.15 * data_component,
        }
        confidence = _clip100(sum(confidence_breakdown.values()))

        risk = _clip100(100.0 - market_score + min(vol / 0.06, 1.0) * 25.0 + vix_penalty)
        return DecisionResult(
            ticker=input_data.ticker,
            as_of=input_data.as_of,
            suitability_score=suitability,
            confidence_score=confidence,
            risk_score=risk,
            success_rate_5d=hit5,
            success_rate_10d=hit10,
            sub_scores={
                "surge_score": surge_score,
                "hold_score": hold_score,
                "market_score": market_score,
                "stability_score": stability,
                "anomaly_percentile": anomaly,
                "sector_trend": sector_trend,
                "sector_rotation_score": float(sector_rotation_score or 0.0),
                "regime_confidence": float(input_data.regime_result.confidence_score if input_data.regime_result else 0.0),
                "vix_level": vix,
                "volatility_20d": vol,
                "n_similar": float(n),
                "n_unique_dates": float(getattr(input_data.similarity, "n_unique_dates", n)),
                "avg_similarity": float(input_data.similarity.avg_similarity),
            },
            score_breakdown=score_breakdown,
            confidence_breakdown=confidence_breakdown,
            label=_label(suitability),
        )
