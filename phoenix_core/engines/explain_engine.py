from __future__ import annotations

from ..interfaces import ExplainEngine as ExplainEngineInterface
from ..models import DecisionResult
from ..registry import EngineRegistry


def _top_positive(breakdown: dict[str, float]) -> tuple[str, float] | None:
    positives = [(k, v) for k, v in breakdown.items() if v > 0]
    return max(positives, key=lambda x: x[1]) if positives else None


def _top_negative(breakdown: dict[str, float]) -> tuple[str, float] | None:
    negatives = [(k, v) for k, v in breakdown.items() if v < 0]
    return min(negatives, key=lambda x: x[1]) if negatives else None


@EngineRegistry.register("explain_engine", "template_v1")
class TemplateExplainEngine(ExplainEngineInterface):
    name = "template_v1"

    def run(self, input_data: DecisionResult) -> str:
        s = input_data.sub_scores
        lines = []

        if input_data.suitability_score >= 75:
            lines.append("단타 적합도는 강한 편입니다.")
        elif input_data.suitability_score >= 60:
            lines.append("단타 적합도는 관심권입니다.")
        elif input_data.suitability_score >= 45:
            lines.append("단타 적합도는 중립권이므로 확인 매매가 유리합니다.")
        else:
            lines.append("단타 적합도는 낮아 보수적인 접근이 필요합니다.")

        if s.get("market_score", 0) >= 70:
            lines.append("시장 우호도는 양호합니다.")
        elif s.get("market_score", 0) <= 45:
            lines.append("시장 우호도가 낮아 신호의 신뢰를 낮춥니다.")
        else:
            lines.append("시장 흐름은 중립권입니다.")

        if s.get("sector_rotation_score", 0) >= 70 or s.get("sector_trend", 0) > 0.02:
            lines.append("해당 업종 ETF 흐름은 강한 편입니다.")
        elif s.get("sector_rotation_score", 50) <= 40 or s.get("sector_trend", 0) < -0.02:
            lines.append("해당 업종 ETF 흐름은 약한 편이라 최종 점수를 낮췄습니다.")
        else:
            lines.append("업종 흐름은 뚜렷한 우위가 크지 않습니다.")

        if s.get("anomaly_percentile", 0) >= 70:
            lines.append("현재 차트는 과거 대비 매우 특이한 구간입니다. 이는 상승 예측이 아니라 Pattern Rarity가 높다는 뜻입니다.")
        elif s.get("anomaly_percentile", 0) >= 40:
            lines.append("현재 차트의 Pattern Rarity는 중간 이상입니다.")
        else:
            lines.append("현재 차트의 Pattern Rarity는 평범한 범위입니다.")

        pos = _top_positive(input_data.score_breakdown)
        neg = _top_negative(input_data.score_breakdown)
        if pos:
            lines.append(f"가장 큰 긍정 요인은 {pos[0]}(+{pos[1]:.1f})입니다.")
        if neg:
            lines.append(f"가장 큰 감점 요인은 {neg[0]}({neg[1]:.1f})입니다.")

        lines.append(
            f"과거 유사 사례 {int(s.get('n_similar', 0))}건 기준, "
            f"5거래일 +5% 도달률은 {input_data.success_rate_5d * 100:.0f}%, "
            f"10거래일 +10% 도달률은 {input_data.success_rate_10d * 100:.0f}%입니다."
        )
        if input_data.confidence_score >= 80:
            lines.append("유사 사례 수와 평균 유사도가 충분해 신뢰도는 높은 편입니다.")
        elif input_data.confidence_score < 50:
            lines.append("유사 사례 수 또는 유사도가 부족해 신뢰도는 낮은 편입니다.")
        return " ".join(lines)
