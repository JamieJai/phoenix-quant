from __future__ import annotations

from ..interfaces import Engine
from ..models import RankingInput, RankingItem, RankingResult
from ..pipeline import analyze_ticker_quiet, build_trade_plan
from ..registry import EngineRegistry


@EngineRegistry.register("ranking_engine", "ranking_v1")
class RankingEngine(Engine[RankingInput, RankingResult]):
    """Universe 전체를 분석해 상위 종목을 반환한다.

    v1은 기존 단일 티커 파이프라인을 재사용하는 안전한 구현이다. 속도 최적화는 향후 배치형으로 개선한다.
    """

    slot = "ranking_engine"
    name = "ranking_v1"

    def run(self, input_data: RankingInput) -> RankingResult:
        items: list[RankingItem] = []
        for ticker in input_data.universe:
            try:
                decision, meta = analyze_ticker_quiet(
                    input_data.config,
                    ticker,
                    period=input_data.period,
                    refresh=False,
                    retrain=False,
                    k=input_data.k,
                    raw_data=input_data.raw_data,
                    prebuilt=input_data.prebuilt,
                )
                trade_plan = build_trade_plan(meta["latest_close"], input_data.config)
                items.append(RankingItem(
                    ticker=ticker,
                    as_of=decision.as_of,
                    suitability_score=decision.suitability_score,
                    confidence_score=decision.confidence_score,
                    risk_score=decision.risk_score,
                    label=decision.label,
                    market_score=decision.sub_scores.get("market_score", 0.0),
                    sector_score=decision.sub_scores.get("sector_rotation_score", decision.sub_scores.get("market_score", 0.0)),
                    pattern_rarity=decision.sub_scores.get("anomaly_percentile", 0.0),
                    hit_rate_5d=decision.success_rate_5d,
                    entry_price=float(trade_plan["entry_price"]),
                    take_profit_price=float(trade_plan["take_profit_price"]),
                    stop_loss_price=float(trade_plan["stop_loss_price"]),
                    max_hold_days=int(trade_plan["max_hold_days"]),
                ))
            except Exception as exc:  # noqa: BLE001
                if input_data.verbose:
                    print(f"[rank warn] {ticker}: {exc}")
        items.sort(key=lambda x: (x.suitability_score, x.confidence_score), reverse=True)
        return RankingResult(as_of=items[0].as_of if items else input_data.as_of, items=items[:input_data.top_n])
