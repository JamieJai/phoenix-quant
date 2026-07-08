from __future__ import annotations

import os
from datetime import date
from typing import Dict, Iterable, List

import pandas as pd

from . import bootstrap
from .config import AppConfig
from .data_loader import download_ohlcv
from .default_features import BASELINE_FEATURE_NAMES
from .labels import compute_forward_labels, row_to_label_dict
from .models import (
    ContextEngineInput,
    CorrelationInput,
    DecisionInput,
    FeatureEngineInput,
    FeatureVector,
    PatternEngineInput,
    MarketRegimeInput,
    PatternRecord,
    RankingInput,
    SectorRotationInput,
    SimilarityQuery,
)
from .registry import EngineRegistry
from .trade import EntryMode, SameDayRule, TradeConfig
from .trade.trade_rules import normalize_config, stop_loss_price, take_profit_price, trailing_stop_price


PHOENIX_QUANT_VERSION = "v2.1.1"


def build_pattern_records(raw_data: Dict[str, pd.DataFrame], feature_engine, feature_names: List[str] | None = None) -> List[PatternRecord]:
    feature_names = feature_names or BASELINE_FEATURE_NAMES
    records: list[PatternRecord] = []
    for ticker, df in raw_data.items():
        if ticker.startswith("^") or ticker in {"SPY", "QQQ", "DIA", "IWM", "SMH", "SOXX"}:
            continue
        try:
            feats = feature_engine.compute_frame(df, feature_names)
            labels = compute_forward_labels(df)
            valid = feats.dropna(subset=feature_names)
            for idx, row in valid.iterrows():
                values = {f: float(row[f]) for f in feature_names}
                label_dict = row_to_label_dict(labels, idx) if idx in labels.index else {}
                records.append(PatternRecord(
                    ticker=ticker.upper(),
                    date=pd.Timestamp(idx).date(),
                    feature_vector=FeatureVector(ticker=ticker.upper(), as_of=pd.Timestamp(idx).date(), values=values),
                    forward_labels=label_dict,
                ))
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] {ticker} 레코드 생성 실패: {exc}")
    return records


def get_or_train_pattern_engine(config: AppConfig, records: List[PatternRecord], retrain: bool = False):
    path = os.path.join(config.models_dir, "pattern_isolation_forest.joblib")
    engine = EngineRegistry.get("pattern_engine", config.engines["pattern_engine"], feature_names=BASELINE_FEATURE_NAMES)
    if not retrain and os.path.exists(path):
        try:
            engine.load(path)
            return engine
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] PatternEngine 로드 실패, 재학습: {exc}")
    engine.fit(records)
    engine.save(path)
    return engine


def get_or_build_similarity_engine(config: AppConfig, records: List[PatternRecord], retrain: bool = False):
    path = os.path.join(config.models_dir, "similarity_cosine_knn.joblib")
    engine = EngineRegistry.get("similarity_engine", config.engines["similarity_engine"],
                                feature_names=BASELINE_FEATURE_NAMES, k=config.similarity_k)
    if not retrain and os.path.exists(path):
        try:
            engine.load(path)
            return engine
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] SimilarityEngine 로드 실패, 재구축: {exc}")
    engine.build(records)
    engine.save(path)
    return engine



def _prepare_artifacts(config: AppConfig, tickers: list[str], period: str, refresh: bool, retrain: bool):
    os.makedirs(config.cache_dir, exist_ok=True)
    os.makedirs(config.models_dir, exist_ok=True)
    os.makedirs(config.reports_dir, exist_ok=True)
    raw_data = download_ohlcv(tickers, cache_dir=config.cache_dir, period=period, force_refresh=refresh)
    feature_engine = EngineRegistry.get("feature_engine", config.engines["feature_engine"], feature_names=BASELINE_FEATURE_NAMES)
    records = build_pattern_records(raw_data, feature_engine, BASELINE_FEATURE_NAMES)
    if len(records) < 100:
        raise RuntimeError(f"PatternRecord가 너무 적습니다: {len(records)}")
    pattern_engine = get_or_train_pattern_engine(config, records, retrain=retrain)
    similarity_engine = get_or_build_similarity_engine(config, records, retrain=retrain)
    return {
        "raw_data": raw_data,
        "feature_engine": feature_engine,
        "records": records,
        "pattern_engine": pattern_engine,
        "similarity_engine": similarity_engine,
    }


def analyze_ticker_quiet(config: AppConfig, ticker: str, period: str = "3y", refresh: bool = False,
                         retrain: bool = False, k: int | None = None,
                         raw_data: dict | None = None, prebuilt: dict | None = None):
    """리포트 출력 없이 DecisionResult와 메타만 반환. Ranking Engine이 재사용한다."""
    bootstrap.init()
    ticker = ticker.upper()
    k = k or config.similarity_k
    tickers = list(dict.fromkeys(config.universe + config.market_etfs + [ticker]))
    if prebuilt is None:
        prebuilt = _prepare_artifacts(config, tickers, period, refresh, retrain)
    raw_data = raw_data or prebuilt["raw_data"]
    if ticker not in raw_data:
        raise RuntimeError(f"{ticker} 데이터를 가져오지 못했습니다.")
    for required in ["SPY", "QQQ"]:
        if required not in raw_data:
            raise RuntimeError(f"시장 ETF {required} 데이터를 가져오지 못했습니다.")

    feature_engine = prebuilt["feature_engine"]
    pattern_engine = prebuilt["pattern_engine"]
    similarity_engine = prebuilt["similarity_engine"]

    target_vector = feature_engine.run(FeatureEngineInput(ticker=ticker, ohlcv=raw_data[ticker]))
    latest_close = float(raw_data[ticker].loc[pd.Timestamp(target_vector.as_of), "Close"])
    sector_etf = config.sector_etf_for(ticker)

    context_engine = EngineRegistry.get("context_engine", config.engines["context_engine"])
    market_context = context_engine.run(ContextEngineInput(
        as_of=target_vector.as_of,
        market_ohlcv=raw_data,
        sector_etf=sector_etf,
    ))

    regime_engine = EngineRegistry.get("regime_engine", config.engines.get("regime_engine", "regime_v1"))
    regime_result = regime_engine.run(MarketRegimeInput(as_of=target_vector.as_of, market_ohlcv=raw_data))

    sector_engine = EngineRegistry.get("sector_rotation_engine", config.engines.get("sector_rotation_engine", "rotation_v1"))
    sector_rotation = sector_engine.run(SectorRotationInput(
        as_of=target_vector.as_of,
        market_ohlcv=raw_data,
        target_sector_etf=sector_etf,
        sector_etfs=[e for e in config.market_etfs if not e.startswith("^")],
    ))

    compare = ["SPY", "QQQ", sector_etf, "NVDA", "AMD", "AVGO", "QCOM", "INTC"]
    corr_engine = EngineRegistry.get("correlation_engine", config.engines.get("correlation_engine", "correlation_v1"))
    correlation_result = corr_engine.run(CorrelationInput(
        ticker=ticker,
        as_of=target_vector.as_of,
        ohlcv=raw_data,
        compare_tickers=compare,
    ))

    pattern_result = pattern_engine.run(PatternEngineInput(feature_vector=target_vector))
    similarity_result = similarity_engine.run(SimilarityQuery(
        feature_vector=target_vector,
        k=k,
        exclude_ticker=ticker,
        exclude_recent_days=30,
        similarity_threshold=config.similarity_threshold,
    ))
    decision_engine = EngineRegistry.get("decision_engine", config.engines["decision_engine"],
                                         min_trades_for_confidence=config.backtest.get("min_trades_for_confidence", 20))
    decision = decision_engine.run(DecisionInput(
        ticker=ticker,
        as_of=target_vector.as_of,
        pattern=pattern_result,
        similarity=similarity_result,
        market_context=market_context,
        feature_vector=target_vector,
        regime_result=regime_result,
        sector_rotation=sector_rotation,
        correlation_result=correlation_result,
    ))
    explain_engine = EngineRegistry.get("explain_engine", config.engines["explain_engine"])
    decision.explanation = explain_engine.run(decision)
    return decision, {
        "latest_close": latest_close,
        "sector_etf": sector_etf,
        "market_context": market_context,
        "regime_result": regime_result,
        "sector_rotation": sector_rotation,
        "correlation_result": correlation_result,
        "similarity_result": similarity_result,
        "records": len(prebuilt["records"]),
        "prebuilt": prebuilt,
        "raw_data": raw_data,
    }


def analyze_ticker(config: AppConfig, ticker: str, period: str = "3y", refresh: bool = False,
                   retrain: bool = False, k: int | None = None) -> tuple[str, dict]:
    bootstrap.init()
    ticker = ticker.upper()
    tickers = list(dict.fromkeys(config.universe + config.market_etfs + [ticker]))
    print("[1/6] 데이터/아티팩트 준비 중...")
    prebuilt = _prepare_artifacts(config, tickers, period, refresh, retrain)
    print("[2/6] 단일 티커 분석 중...")
    decision, meta = analyze_ticker_quiet(config, ticker, period=period, refresh=False, retrain=False, k=k,
                                          raw_data=prebuilt["raw_data"], prebuilt=prebuilt)
    print("[3/6] 리포트 생성 중...")
    report = render_report(
        ticker,
        meta["latest_close"],
        meta["sector_etf"],
        meta["market_context"],
        meta["similarity_result"],
        decision,
        regime_result=meta["regime_result"],
        sector_rotation=meta["sector_rotation"],
        correlation_result=meta["correlation_result"],
        trade_plan=build_trade_plan(meta["latest_close"], config),
    )
    report_path = os.path.join(config.reports_dir, f"{ticker}_{decision.as_of}.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    meta["report_path"] = report_path
    meta["decision"] = decision
    return report, meta


def rank_universe(config: AppConfig, period: str = "3y", refresh: bool = False,
                  retrain: bool = False, top_n: int = 20, k: int | None = None):
    bootstrap.init()
    k = k or config.similarity_k
    tickers = list(dict.fromkeys(config.universe + config.market_etfs))
    print("[1/4] 데이터/아티팩트 준비 중...")
    prebuilt = _prepare_artifacts(config, tickers, period, refresh, retrain)
    raw_data = prebuilt["raw_data"]
    as_of = max(df.index.max() for df in raw_data.values()).date()
    print("[2/4] Ranking Engine 실행 중...")
    ranking_engine = EngineRegistry.get("ranking_engine", config.engines.get("ranking_engine", "ranking_v1"))
    ranking = ranking_engine.run(RankingInput(
        config=config,
        universe=config.universe,
        as_of=as_of,
        raw_data=raw_data,
        prebuilt=prebuilt,
        period=period,
        top_n=top_n,
        k=k,
        verbose=True,
    ))
    report = render_ranking_report(ranking)
    report_path = os.path.join(config.reports_dir, f"ranking_{ranking.as_of}.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    return report, {"report_path": report_path, "ranking": ranking}

def _unique_similar_cases(neighbors, max_cases: int = 5, per_ticker_limit: int = 2):
    """화면 표시용 유사 사례 중복 완화.

    검색 자체는 Top-K 전체를 쓰되, 사용자에게 보여줄 때만 같은 ticker가 과도하게
    반복되는 것을 줄인다. 같은 종목은 최대 per_ticker_limit개까지만 표시한다.
    """
    counts = {}
    picked = []
    for n in neighbors:
        c = counts.get(n.ticker, 0)
        if c >= per_ticker_limit:
            continue
        picked.append(n)
        counts[n.ticker] = c + 1
        if len(picked) >= max_cases:
            break
    return picked


def _format_breakdown(breakdown: dict[str, float], total_label: str | None = None) -> str:
    if not breakdown:
        return "  - 없음"
    lines = []
    for key, val in breakdown.items():
        sign = "+" if val >= 0 else ""
        lines.append(f"  - {key}: {sign}{val:.1f}")
    if total_label:
        lines.append(f"  = {total_label}")
    return "\n".join(lines)


def build_trade_config(config: AppConfig) -> TradeConfig:
    raw = getattr(config, "trade", {}) or {}
    cfg = TradeConfig(
        take_profit=float(raw.get("take_profit", 0.05)),
        stop_loss=float(raw.get("stop_loss", 0.03)),
        max_hold_days=int(raw.get("max_hold_days", 5)),
        trailing_stop=raw.get("trailing_stop"),
        entry_mode=raw.get("entry_mode", EntryMode.CLOSE),
        same_day_rule=raw.get("same_day_rule", SameDayRule.STOP_FIRST),
        fee_bps=float(raw.get("fee_bps", 1.5)),
        slippage_bps=float(raw.get("slippage_bps", 5.0)),
    )
    return normalize_config(cfg)


def build_trade_plan(reference_price: float, config: AppConfig) -> dict[str, float | int | str | None]:
    cfg = build_trade_config(config)
    entry_price = float(reference_price)
    tr_price = trailing_stop_price(entry_price, cfg)
    entry_mode = cfg.entry_mode.value if hasattr(cfg.entry_mode, "value") else str(cfg.entry_mode)
    entry_label = "기준일 종가"
    if cfg.entry_mode == EntryMode.NEXT_OPEN:
        entry_label = "다음 장 시가 기준(현재 기준가로 가격대 산정)"
    return {
        "entry_price": entry_price,
        "entry_label": entry_label,
        "entry_mode": entry_mode,
        "take_profit_price": take_profit_price(entry_price, cfg),
        "stop_loss_price": stop_loss_price(entry_price, cfg),
        "trailing_stop_price": tr_price,
        "take_profit_pct": float(cfg.take_profit),
        "stop_loss_pct": float(cfg.stop_loss),
        "trailing_stop_pct": float(cfg.trailing_stop) if cfg.trailing_stop is not None else None,
        "max_hold_days": int(cfg.max_hold_days),
        "round_trip_cost_pct": float(cfg.round_trip_cost()),
    }


def _format_trade_plan(trade_plan: dict | None) -> list[str]:
    if not trade_plan:
        return []
    lines = [
        "Trade Plan:",
        f"  - 진입 기준가: ${trade_plan['entry_price']:.2f} ({trade_plan['entry_label']})",
        f"  - 목표 매도가: ${trade_plan['take_profit_price']:.2f} (+{trade_plan['take_profit_pct'] * 100:.1f}%)",
        f"  - 손절가: ${trade_plan['stop_loss_price']:.2f} (-{trade_plan['stop_loss_pct'] * 100:.1f}%)",
        f"  - 최대 보유: {trade_plan['max_hold_days']}거래일 / 예상 왕복비용: {trade_plan['round_trip_cost_pct'] * 100:.2f}%",
    ]
    if trade_plan.get("trailing_stop_price") is not None:
        lines.append(
            f"  - 트레일링 스탑: ${trade_plan['trailing_stop_price']:.2f} (-{trade_plan['trailing_stop_pct'] * 100:.1f}% from high)"
        )
    lines.append("")
    return lines


def render_report(ticker, latest_close, sector_etf, market_context, similarity_result, decision,
                  regime_result=None, sector_rotation=None, correlation_result=None, trade_plan=None) -> str:
    stars = lambda score: "★" * int(round(score / 20)) + "☆" * (5 - int(round(score / 20)))
    display_neighbors = _unique_similar_cases(similarity_result.neighbors[:30], max_cases=5, per_ticker_limit=1)
    similar_cases = "\n".join(
        [f"  - {n.ticker} / {n.date} / 유사도 {n.similarity:.3f} / 5D+5% {n.labels.get('hit_5pct_5d', 0):.0f}" for n in display_neighbors]
    ) or "  - 없음"

    regime_lines = []
    if regime_result:
        regime_lines = [
            "Market Regime:",
            f"  - Regime: {regime_result.regime}",
            f"  - Regime Confidence: {regime_result.confidence_score:.0f}/100 {stars(regime_result.confidence_score)}",
            f"  - Momentum: {regime_result.momentum_score:.0f}/100 / Breadth: {regime_result.breadth_score:.0f}/100 / Volatility: {regime_result.volatility_score:.0f}/100",
            "",
        ]

    sector_lines = []
    if sector_rotation:
        top = ", ".join([f"{s.etf} {s.score:.0f}" for s in sector_rotation.top_sectors[:5]]) or "없음"
        if sector_rotation.target_strength:
            ts = sector_rotation.target_strength
            target = f"{ts.etf} {ts.score:.0f}/100 / 5D {ts.return_5d*100:.2f}% / 20D {ts.return_20d*100:.2f}% / Rank {ts.rank}"
        else:
            target = f"{sector_etf} 데이터 부족"
        sector_lines = [
            "Sector Rotation:",
            f"  - Target: {target}",
            f"  - Top Sectors: {top}",
            "",
        ]

    corr_lines = []
    if correlation_result:
        strongest = correlation_result.strongest("corr_90d", limit=5)
        if strongest:
            txt = ", ".join([f"{t}:{v:.2f}" for t, v in strongest])
            corr_lines = ["Correlation Map:", f"  - 90D strongest: {txt}", ""]

    return "\n".join([
        f"Phoenix Quant {PHOENIX_QUANT_VERSION}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"Ticker: {ticker}",
        f"기준일: {decision.as_of}",
        f"기준가: ${latest_close:.2f}",
        "",
        f"단타 적합도: {decision.suitability_score:.0f}/100 {stars(decision.suitability_score)} ({decision.label})",
        f"신뢰도: {decision.confidence_score:.0f}/100 {stars(decision.confidence_score)}",
        f"위험도: {decision.risk_score:.0f}/100",
        "",
        *regime_lines,
        *sector_lines,
        *corr_lines,
        *_format_trade_plan(trade_plan),
        "Decision Breakdown:",
        _format_breakdown(decision.score_breakdown, f"최종 {decision.suitability_score:.1f}/100"),
        "",
        "Confidence Breakdown:",
        _format_breakdown(decision.confidence_breakdown, f"신뢰도 {decision.confidence_score:.1f}/100"),
        "",
        f"시장 우호도: {decision.sub_scores['market_score']:.0f}/100 {stars(decision.sub_scores['market_score'])}",
        f"업종 ETF: {sector_etf} / 업종 20일 추세: {decision.sub_scores['sector_trend']*100:.2f}% / Rotation Score: {decision.sub_scores.get('sector_rotation_score',0):.0f}",
        f"Pattern Rarity: {decision.sub_scores['anomaly_percentile']:.0f}/100 (특이도, 상승확률 아님)",
        f"유지 점수: {decision.sub_scores['hold_score']:.0f}/100",
        f"VIX: {market_context.vix_level:.2f} / Legacy Regime: {market_context.regime} / Risk: {market_context.risk_level}",
        "",
        f"과거 유사 사례: {similarity_result.n_similar}건 (유사도 {similarity_result.avg_similarity:.3f} 평균, Top-{len(similarity_result.neighbors)})",
        f"5거래일 +5% 도달률: {decision.success_rate_5d*100:.0f}%",
        f"10거래일 +10% 도달률: {decision.success_rate_10d*100:.0f}%",
        "",
        "Top Similar Cases (동일 종목 최대 1개 표시):",
        similar_cases,
        "",
        f"AI Summary: {decision.explanation}",
        "",
        "※ 본 결과는 과거 패턴 기반 통계적 참고 자료이며, 투자 자문이 아닙니다.",
    ])


def render_ranking_report(ranking) -> str:
    lines = [
        f"Phoenix Quant {PHOENIX_QUANT_VERSION} Ranking",
        "━━━━━━━━━━━━━━━━━━━━",
        f"기준일: {ranking.as_of}",
        "",
        "Rank | Ticker | Suitability | Confidence | Risk | Market | Entry | TP | SL | Hold | 5D Hit | Label",
    ]
    for i, item in enumerate(ranking.items, start=1):
        lines.append(
            f"{i:>2} | {item.ticker:<6} | {item.suitability_score:>5.1f} | {item.confidence_score:>5.1f} | "
            f"{item.risk_score:>5.1f} | {item.market_score:>5.1f} | "
            f"${item.entry_price:>7.2f} | ${item.take_profit_price:>7.2f} | ${item.stop_loss_price:>7.2f} | "
            f"{item.max_hold_days:>2}d | {item.hit_rate_5d*100:>5.0f}% | {item.label}"
        )
    lines += ["", "※ Ranking은 같은 기준의 상대 비교용이며 투자 자문이 아닙니다."]
    return "\n".join(lines)
