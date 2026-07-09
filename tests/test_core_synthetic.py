from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from phoenix_core import bootstrap
from phoenix_core.config import load_config
from phoenix_core.default_features import BASELINE_FEATURE_NAMES
from phoenix_core.engines.feature_engine import CatalogFeatureEngine
from phoenix_core.engines.intraday_context_engine import IntradayContext
from phoenix_core.engines.statistical_validation_engine import StatisticalValidationEngine, ValidationConfig
from phoenix_core.labels import compute_forward_labels
from phoenix_core.intraday_features import INTRADAY_FEATURE_NAMES, build_intraday_feature_dict
from phoenix_core.intraday_feature_store import append_intraday_feature_rows, load_intraday_feature_cache
from phoenix_core.intraday_overlay_ranker import rank_intraday_overlay_contexts
from phoenix_core.services.intraday_message_formatter import extract_candidate_tickers, format_intraday_overlay
from phoenix_core.services.telegram_message_formatter import compact_analysis_output, compact_ranking_output, help_message
from phoenix_core.models import (
    CorrelationInput,
    ContextEngineInput,
    DecisionInput,
    FeatureEngineInput,
    MarketRegimeInput,
    PatternEngineInput,
    SectorRotationInput,
    SimilarityQuery,
    SimilarityResult,
)
from phoenix_core.pipeline import build_pattern_records, build_trade_plan
from phoenix_core.registry import EngineRegistry
from phoenix_core.trade import EntryMode, TradeConfig, TradeSimulationEngine
from phoenix_core.services import telegram_command_bot as telegram_bot_module
from phoenix_core.services.telegram_command_bot import PhoenixTelegramBot


def make_synthetic_ohlcv(n=420, seed=0, drift=0.0004, vol=0.02, start_price=50.0):
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    for idx in rng.choice(range(80, n - 40), size=max(1, n // 120), replace=False):
        rets[idx:idx + 5] += rng.uniform(0.015, 0.04, 5)
    close = start_price * np.exp(np.cumsum(rets))
    high = close * (1 + rng.uniform(0.0, 0.02, n))
    low = close * (1 - rng.uniform(0.0, 0.02, n))
    open_ = close * (1 + rng.normal(0, 0.005, n))
    volume = rng.uniform(1e6, 5e6, n) * (1 + np.abs(rets) * 20)
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    df = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx)
    df.index.name = "Date"
    return df


def main():
    print("=== Phoenix Core Synthetic Test ===")
    bootstrap.init()
    registered = EngineRegistry.list_all()
    print("registered:", registered)
    for slot in ["feature_engine", "pattern_engine", "similarity_engine", "decision_engine", "explain_engine", "backtest_engine"]:
        assert slot in registered and registered[slot]

    raw = {f"SYN{i}": make_synthetic_ohlcv(seed=i) for i in range(15)}
    raw.update({
        "SPY": make_synthetic_ohlcv(seed=100, drift=0.0002, vol=0.012),
        "QQQ": make_synthetic_ohlcv(seed=101, drift=0.00025, vol=0.014),
        "DIA": make_synthetic_ohlcv(seed=102, drift=0.00015, vol=0.010),
        "IWM": make_synthetic_ohlcv(seed=103, drift=0.0001, vol=0.016),
        "SMH": make_synthetic_ohlcv(seed=104, drift=0.00045, vol=0.018),
        "SOXX": make_synthetic_ohlcv(seed=105, drift=0.0005, vol=0.018),
        "^VIX": make_synthetic_ohlcv(seed=106, drift=0.0, vol=0.03, start_price=18.0),
    })
    target = "SYN99"
    raw[target] = make_synthetic_ohlcv(seed=999, drift=0.001, vol=0.025)

    feature_engine = EngineRegistry.get("feature_engine", "catalog_v1", feature_names=BASELINE_FEATURE_NAMES)
    fv = feature_engine.run(FeatureEngineInput(ticker=target, ohlcv=raw[target]))
    assert set(BASELINE_FEATURE_NAMES).issubset(fv.values.keys())
    assert all(np.isfinite(list(fv.values.values())))
    print("feature vector ok", fv.as_of)

    # Label NaN propagation check: 마지막 10영업일은 10D 라벨이 확정되면 안 됨.
    labels = compute_forward_labels(raw[target])
    assert labels["fwd_max_ret_10d"].tail(10).isna().all()
    assert labels["hit_10pct_10d"].tail(10).isna().all()
    print("label NaN propagation ok")

    validation_engine = StatisticalValidationEngine(ValidationConfig(bootstrap_iterations=0))
    ci_low, ci_high = validation_engine.block_bootstrap_ci(
        pd.DataFrame({"as_of": ["2024-01-01", "2024-01-01", "2024-01-02"], "ret": [0.01, 0.03, -0.01]}),
        value_col="ret",
        group_col="as_of",
    )
    assert np.isclose(ci_low, 0.01) and np.isclose(ci_high, 0.01)
    print("statistical validation zero-bootstrap fallback ok")

    # Leakage check: 미래 구간을 바꿔도 과거 시점 feature가 변하지 않아야 함.
    df_a = raw[target].copy()
    df_b = df_a.copy()
    cutoff = 220
    df_b.iloc[cutoff + 1:] = df_b.iloc[cutoff + 1:] * 10
    feats_a = feature_engine.compute_frame(df_a)
    feats_b = feature_engine.compute_frame(df_b)
    assert np.allclose(feats_a.iloc[cutoff].values, feats_b.iloc[cutoff].values, equal_nan=True)
    print("feature leakage check ok")

    records = build_pattern_records(raw, feature_engine, BASELINE_FEATURE_NAMES)
    print("records", len(records))
    assert len(records) > 1000

    pattern_engine = EngineRegistry.get("pattern_engine", "isolation_forest", feature_names=BASELINE_FEATURE_NAMES, n_estimators=80)
    pattern_engine.fit(records)
    pattern = pattern_engine.run(PatternEngineInput(feature_vector=fv))
    assert 0 <= pattern.anomaly_percentile <= 100
    print("pattern ok", pattern.anomaly_percentile)

    similarity_engine = EngineRegistry.get("similarity_engine", "cosine_knn", feature_names=BASELINE_FEATURE_NAMES, k=30)
    similarity_engine.build(records)
    sim = similarity_engine.run(SimilarityQuery(feature_vector=fv, k=30, exclude_ticker=target))
    assert len(sim.neighbors) > 0
    assert 0 <= sim.hit_rate_5d <= 1
    assert sim.n_unique_dates <= len(sim.neighbors)
    print("similarity ok", sim.n_similar, sim.hit_rate_5d)

    context_engine = EngineRegistry.get("context_engine", "market_v1")
    ctx = context_engine.run(ContextEngineInput(as_of=fv.as_of, market_ohlcv=raw, sector_etf="SOXX"))
    assert 0 <= ctx.market_score <= 100
    print("context ok", ctx.market_score)

    cutoff_date = raw[target].index[250].date()
    raw_future_changed = {k: v.copy() for k, v in raw.items()}
    for df in raw_future_changed.values():
        future_mask = df.index > pd.Timestamp(cutoff_date)
        df.loc[future_mask, ["Open", "High", "Low", "Close"]] *= 100.0
        df.loc[future_mask, "Volume"] *= 100.0
    sliced_raw = {k: v[v.index <= pd.Timestamp(cutoff_date)].copy() for k, v in raw.items()}

    ctx_full = context_engine.run(ContextEngineInput(as_of=cutoff_date, market_ohlcv=raw_future_changed, sector_etf="SOXX"))
    ctx_sliced = context_engine.run(ContextEngineInput(as_of=cutoff_date, market_ohlcv=sliced_raw, sector_etf="SOXX"))
    assert np.isclose(ctx_full.market_score, ctx_sliced.market_score)

    regime_engine = EngineRegistry.get("regime_engine", "regime_v1")
    regime_full = regime_engine.run(MarketRegimeInput(as_of=cutoff_date, market_ohlcv=raw_future_changed))
    regime_sliced = regime_engine.run(MarketRegimeInput(as_of=cutoff_date, market_ohlcv=sliced_raw))
    assert regime_full.components == regime_sliced.components

    sector_engine = EngineRegistry.get("sector_rotation_engine", "rotation_v1")
    sector_full = sector_engine.run(SectorRotationInput(as_of=cutoff_date, market_ohlcv=raw_future_changed, target_sector_etf="SOXX"))
    sector_sliced = sector_engine.run(SectorRotationInput(as_of=cutoff_date, market_ohlcv=sliced_raw, target_sector_etf="SOXX"))
    assert [(s.etf, s.score) for s in sector_full.all_strengths] == [(s.etf, s.score) for s in sector_sliced.all_strengths]

    corr_engine = EngineRegistry.get("correlation_engine", "correlation_v1")
    corr_full = corr_engine.run(CorrelationInput(target, cutoff_date, raw_future_changed, ["SPY", "QQQ", "SOXX"]))
    corr_sliced = corr_engine.run(CorrelationInput(target, cutoff_date, sliced_raw, ["SPY", "QQQ", "SOXX"]))
    assert corr_full.correlations == corr_sliced.correlations
    print("as_of defensive slicing ok")

    decision_engine = EngineRegistry.get("decision_engine", "weighted_v1", min_trades_for_confidence=10)
    sim_dedup = SimilarityResult(
        query_ticker=target,
        query_date=fv.as_of,
        neighbors=sim.neighbors,
        n_similar=12,
        hit_rate_5d=0.5,
        hit_rate_10d=0.4,
        n_unique_dates=3,
        avg_similarity=0.6,
    )
    decision = decision_engine.run(DecisionInput(target, fv.as_of, pattern, sim_dedup, ctx, fv))
    assert 0 <= decision.suitability_score <= 100
    assert 0 <= decision.confidence_score <= 100
    assert decision.sub_scores["n_unique_dates"] == 3.0
    assert decision.confidence_breakdown["similar_case_count"] == 10.5
    explanation = EngineRegistry.get("explain_engine", "template_v1").run(decision)
    assert "과거 유사 사례" in explanation
    print("decision/explain ok", decision.suitability_score, explanation)

    backtest = EngineRegistry.get("backtest_engine", "as_of_v1")
    result = backtest.evaluate(records, decision_fn=lambda r: r.feature_vector.values["ret_20d"] > 0.05)
    assert result.n_records > 0
    print("backtest ok", result.n_trades, result.hit_rate)

    experiment_engine = EngineRegistry.get("experiment_engine", "xgb_compare", cv=4, metric="accuracy", random_state=42)
    exp_rows = []
    for r in records:
        row = {"as_of": pd.Timestamp(r.date)}
        row.update(r.feature_vector.values)
        row["label"] = int(float(r.forward_labels.get("hit_5pct_5d", 0.0)) > 0.0)
        exp_rows.append(row)
    exp_df = pd.DataFrame(exp_rows).sort_values("as_of").reset_index(drop=True)
    exp_X = exp_df[BASELINE_FEATURE_NAMES]
    exp_y = exp_df["label"]
    exp_result = experiment_engine.compare(exp_X, exp_y, BASELINE_FEATURE_NAMES[:4], BASELINE_FEATURE_NAMES[4:7], experiment_name="xgb_sanity")
    assert exp_result.metric_name == "accuracy"
    assert exp_result.candidate_metric >= 0.0
    print("xgb experiment ok", exp_result.baseline_metric, exp_result.candidate_metric, exp_result.delta)

    intraday_features = build_intraday_feature_dict(
        gap_prev_close_pct=1.2,
        session_return_pct=0.8,
        ret_fast_3bar_pct=0.4,
        ret_slow_2bar_pct=None,
        relative_intraday_volume=1.7,
        vwap_position_pct=0.3,
        pullback_from_intraday_high_pct=-0.9,
        intraday_score=62,
        intraday_risk_score=31,
    )
    assert list(intraday_features.keys()) == INTRADAY_FEATURE_NAMES
    assert np.isnan(intraday_features["ret_slow_2bar_pct"])
    assert intraday_features["intraday_score"] == 62.0
    print("intraday feature schema ok", len(intraday_features))

    ctx_sample = IntradayContext(
        ticker="SYNH",
        timestamp="2024-01-02T10:30:00",
        source="synthetic",
        current_price=101.0,
        previous_close=100.0,
        current_vs_prev_close_pct=1.0,
        day_open=100.5,
        intraday_return_pct=0.5,
        latest_10m_return_pct=0.2,
        latest_30m_return_pct=0.4,
        today_volume=1_500_000.0,
        avg_intraday_volume=1_000_000.0,
        intraday_volume_ratio=1.5,
        vwap=100.7,
        vwap_position_pct=0.3,
        above_vwap=True,
        intraday_high=102.0,
        pullback_from_intraday_high_pct=-1.0,
        intraday_score=62,
        intraday_risk_score=31,
        label="POSITIVE_INTRADAY_CONTEXT",
        notes=[],
        features=intraday_features,
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_path = os.path.join(tmpdir, "intraday_features.csv")
        assert append_intraday_feature_rows([ctx_sample], cache_path) == 1
        cache_df = load_intraday_feature_cache(cache_path)
        assert len(cache_df) == 1
        assert list(cache_df.columns)[-len(INTRADAY_FEATURE_NAMES):] == INTRADAY_FEATURE_NAMES
        assert cache_df.iloc[0]["ticker"] == "SYNH"
    print("intraday feature cache ok")

    weak_first = IntradayContext(
        ticker="WEAK1",
        timestamp="2024-01-02T10:30:00",
        source="synthetic",
        current_price=100.0,
        previous_close=100.0,
        current_vs_prev_close_pct=0.0,
        day_open=100.0,
        intraday_return_pct=0.0,
        latest_10m_return_pct=-0.5,
        latest_30m_return_pct=-0.7,
        today_volume=800_000.0,
        avg_intraday_volume=1_000_000.0,
        intraday_volume_ratio=0.8,
        vwap=101.0,
        vwap_position_pct=-1.0,
        above_vwap=False,
        intraday_high=104.0,
        pullback_from_intraday_high_pct=-3.8,
        intraday_score=25,
        intraday_risk_score=75,
        label="WEAK_INTRADAY_CONTEXT",
        notes=[],
        features=build_intraday_feature_dict(
            gap_prev_close_pct=0.0,
            session_return_pct=0.0,
            ret_fast_3bar_pct=-0.5,
            ret_slow_2bar_pct=-0.7,
            relative_intraday_volume=0.8,
            vwap_position_pct=-1.0,
            pullback_from_intraday_high_pct=-3.8,
            intraday_score=25,
            intraday_risk_score=75,
        ),
    )
    strong_second = IntradayContext(
        ticker="STRG2",
        timestamp="2024-01-02T10:30:00",
        source="synthetic",
        current_price=103.0,
        previous_close=100.0,
        current_vs_prev_close_pct=3.0,
        day_open=101.0,
        intraday_return_pct=2.0,
        latest_10m_return_pct=1.4,
        latest_30m_return_pct=2.5,
        today_volume=2_500_000.0,
        avg_intraday_volume=1_000_000.0,
        intraday_volume_ratio=2.5,
        vwap=101.5,
        vwap_position_pct=1.5,
        above_vwap=True,
        intraday_high=103.4,
        pullback_from_intraday_high_pct=-0.4,
        intraday_score=88,
        intraday_risk_score=25,
        label="STRONG_INTRADAY_MOMENTUM",
        notes=[],
        features=build_intraday_feature_dict(
            gap_prev_close_pct=3.0,
            session_return_pct=2.0,
            ret_fast_3bar_pct=1.4,
            ret_slow_2bar_pct=2.5,
            relative_intraday_volume=2.5,
            vwap_position_pct=1.5,
            pullback_from_intraday_high_pct=-0.4,
            intraday_score=88,
            intraday_risk_score=25,
        ),
    )
    ranked_overlay = rank_intraday_overlay_contexts([weak_first, strong_second])
    assert ranked_overlay[0].context.ticker == "STRG2"
    assert ranked_overlay[0].original_rank == 2
    print("intraday overlay rerank ok", ranked_overlay[0].adjusted_score)

    ranking_text = """Phoenix Quant v1.2 Ranking
기준일: 2024-01-02
Rank | Ticker | Suitability | Confidence | Risk | Market | Sector | Pattern Rarity | 5D Hit | Label
 1 | NVDA   |  71.2 |  88.0 |  31.5 |  60.0 |  80.0 |  95.0 |  42% | 관심
 2 | AMD    |  62.1 |  81.0 |  44.0 |  58.0 |  76.0 |  90.0 |  35% | 관심
"""
    compact_rank = compact_ranking_output(ranking_text, max_rows=2)
    assert "NVDA" in compact_rank and "AMD" in compact_rank and "Rank | Ticker" in compact_rank

    xgb_ranking_text = """Phoenix Quant v2.1.1 Ranking
湲곗??? 2024-01-02
Rank | Ticker | Final | XGB | Suitability | Confidence | Risk | Market | Entry | TP | SL | Hold | 5D Hit | Label
 1 | NVDA   |  74.2 |  81.0 |  71.2 |  88.0 |  31.5 |  60.0 | $ 100.00 | $ 105.00 | $  97.00 |  5d |    42% | 愿??
 2 | AMD    |  65.1 |  72.0 |  62.1 |  81.0 |  44.0 |  58.0 | $ 100.00 | $ 105.00 | $  97.00 |  5d |    35% | 愿??
"""
    compact_xgb_rank = compact_ranking_output(xgb_ranking_text, max_rows=2)
    assert "final" in compact_xgb_rank and "xgb" in compact_xgb_rank and "NVDA" in compact_xgb_rank

    noisy_ranking_text = """Phoenix Quant v2.1.1 Ranking
━━━━━━━━━━━━━━━━━━━━
기준일: 2026-07-08

Rank | Ticker | Final | XGB | Suitability | Confidence | Risk | Market | Entry | TP | SL | Hold | 5D Hit | Label
 1 | PANW   |  45.7 |  57.0 |  40.9 |  90.9 |  59.9 |  54.6 | $ 320.59 | $ 336.62 | $ 310.97 |  5d |    56% | 비추천
 2 | PLTR   |  41.3 |  53.3 |  36.1 |  91.3 |  60.8 |  54.6 | $ 132.22 | $ 138.83 | $ 128.25 |  5d |    50% | 비추천
 3 | TSLA   |  39.1 |  56.7 |  31.6 |  89.3 |  61.8 |  54.6 | $ 394.06 | $ 413.76 | $ 382.24 |  5d |    50% | 비추천
 4 | MU     |  38.0 |  79.0 |  20.4 |  91.0 |  76.2 |  48.8 | $ 948.80 | $ 996.24 | $ 920.34 |  5d |    60% | 비추천
 5 | INTU   |  37.6 |  46.7 |  33.8 |  92.2 |  56.6 |  54.6 | $ 272.10 | $ 285.71 | $ 263.94 |  5d |    41% | 비추천
 6 | INTC   |  37.3 |  82.7 |  17.8 |  92.0 |  75.8 |  48.8 | $ 110.24 | $ 115.75 | $ 106.93 |  5d |    53% | 비추천
 7 | QCOM   |  36.8 |  62.7 |  25.7 |  92.2 |  68.3 |  50.8 | $ 186.56 | $ 195.89 | $ 180.96 |  5d |    59% | 비추천
 8 | CRWD   |  36.2 |  39.7 |  34.7 |  90.7 |  56.0 |  54.6 | $ 191.12 | $ 200.68 | $ 185.39 |  5d |    44% | 비추천
 9 | NFLX   |  31.6 |  36.3 |  29.5 |  91.8 |  56.3 |  54.6 | $  75.59 | $  79.37 | $  73.32 |  5d |    30% | 비추천
10 | AMAT   |  31.5 |  67.7 |  16.0 |  91.0 |  76.2 |  48.8 | $ 570.50 | $ 599.02 | $ 553.38 |  5d |    47% | 비추천
"""
    extracted = extract_candidate_tickers(noisy_ranking_text, limit=20)
    assert extracted == ["PANW", "PLTR", "TSLA", "MU", "INTU", "INTC", "QCOM", "CRWD", "NFLX", "AMAT"]
    assert not {"XGB", "TP", "SL"}.intersection(extracted)

    dotted_ranking_text = """1. PANW   final 45.7 XGB 57.0 TP 336.62 SL 310.97
 2. PLTR   final 41.3
3 | TSLA | final 39.1
Ticker: XGB
"""
    assert extract_candidate_tickers(dotted_ranking_text, limit=10) == ["PANW", "PLTR", "TSLA"]
    print("intraday ticker extraction ok")

    analysis_text = """Phoenix Quant v1.2
Ticker: NVDA
기준일: 2024-01-02
기준가: $100.00
단타 적합도: 70/100
신뢰도: 88/100
위험도: 31/100
Trade Plan:
  - 진입 기준가: $100.00 (기준일 종가)
  - 목표 매도가: $105.00 (+5.0%)
  - 손절가: $97.00 (-3.0%)
  - 최대 보유: 5거래일 / 예상 왕복비용: 0.13%
Decision Breakdown:
  - pattern_contribution: +20.0
AI Summary: sample summary
Top Similar Cases:
  - omitted
"""
    compact_analysis = compact_analysis_output(analysis_text)
    assert "Ticker: NVDA" in compact_analysis and "AI Summary" in compact_analysis and "Top Similar" not in compact_analysis
    assert "목표 매도가" in compact_analysis and "손절가" in compact_analysis

    cfg = load_config(os.path.join(ROOT, "config/config.yaml"))
    plan = build_trade_plan(100.0, cfg)
    assert plan["take_profit_price"] == 105.0
    assert plan["stop_loss_price"] == 97.0
    print("trade plan report schema ok")

    overlay_text = format_intraday_overlay([weak_first, strong_second], max_items=2, rerank=True)
    assert "장중 재정렬" in overlay_text and "STRG2" in overlay_text and "daily #2" in overlay_text
    no_data_ctx = IntradayContext(
        ticker="NODATA",
        timestamp="2024-01-02T10:30:00",
        source="synthetic",
        current_price=None,
        previous_close=None,
        current_vs_prev_close_pct=None,
        day_open=None,
        intraday_return_pct=None,
        latest_10m_return_pct=None,
        latest_30m_return_pct=None,
        today_volume=None,
        avg_intraday_volume=None,
        intraday_volume_ratio=None,
        vwap=None,
        vwap_position_pct=None,
        above_vwap=None,
        intraday_high=None,
        pullback_from_intraday_high_pct=None,
        intraday_score=0,
        intraday_risk_score=100,
        label="NO_DATA",
        notes=[],
        features={},
    )
    overlay_no_data_text = format_intraday_overlay([no_data_ctx, strong_second], max_items=2, rerank=True)
    assert "NODATA" not in overlay_no_data_text and "STRG2" in overlay_no_data_text

    class FakeIntradayEngine:
        def analyze_many(self, tickers):
            mapping = {"NVDA": replace(weak_first, ticker="NVDA"), "AMD": replace(strong_second, ticker="AMD")}
            return [mapping[t] for t in tickers if t in mapping]

    def make_bot(expected_top_n):
        bot = PhoenixTelegramBot.__new__(PhoenixTelegramBot)
        bot.default_top_n = 2
        bot.top_candidate_pool_n = 50
        bot.hot_min_score = 55
        bot.refresh_on_top = False
        bot.intraday_enabled = True
        bot.overlay_enabled = True
        bot.overlay_max = 2
        bot.intraday_overlay_rerank = True
        bot.intraday_feature_cache_enabled = False
        bot.shadow_log_dir = Path(tempfile.mkdtemp())
        bot.intraday_engine = FakeIntradayEngine()
        bot.project_dir = Path(ROOT)
        bot.python_exe = sys.executable
        bot.timeout_sec = 30
        def fake_run(cmd):
            assert cmd[-1] == str(expected_top_n)
            return xgb_ranking_text.replace("NVDA", "NVDA", 1)
        bot._run = fake_run
        return bot

    old_chat_action = telegram_bot_module.send_chat_action_with_token
    telegram_bot_module.send_chat_action_with_token = lambda *args, **kwargs: None
    try:
        fake_profile = type("FakeProfile", (), {"token": "token"})()
        top_resp = make_bot(2)._cmd_top(["2"], "chat", fake_profile)
        assert "Rank | Ticker | Final/XGB" in top_resp
        assert top_resp.index("1. NVDA") < top_resp.index("2. AMD")
        assert "📡 Intraday Overlay" in top_resp
        assert "실험: Daily 후보 50" not in top_resp

        toplive_resp = make_bot(50)._cmd_toplive(["2"], "chat", fake_profile)
        assert "Experimental Intraday Rerank" in toplive_resp
        assert "실험: Daily 후보 50" in toplive_resp
        assert toplive_resp.index("AMD") < toplive_resp.index("NVDA")

        hot_resp = make_bot(50)._cmd_hot(["2"], "chat", fake_profile)
        assert "장중 관심 후보" in hot_resp
        assert "AMD" in hot_resp and "NVDA" not in hot_resp
    finally:
        telegram_bot_module.send_chat_action_with_token = old_chat_action

    assert "/top 10 - 일봉 기반 후보" in help_message()
    assert "/toplive 10 - 실험" in help_message()
    assert "/hot 10 - 장중 강세" in help_message()
    assert "PHOENIX_TOP_INTRADAY_RERANK" not in Path(os.path.join(ROOT, "phoenix_core/services/telegram_command_bot.py")).read_text()
    assert "PHOENIX_TOP_INTRADAY_RERANK" not in Path(os.path.join(ROOT, ".env.example")).read_text()
    print("telegram top/toplive/hot separation ok")
    print("telegram compact formatter ok")

    trade_engine = TradeSimulationEngine(TradeConfig(max_hold_days=2, entry_mode=EntryMode.NEXT_OPEN, take_profit=9.0, stop_loss=9.0, fee_bps=0.0, slippage_bps=0.0))
    trade_idx = pd.date_range("2024-01-01", periods=10, freq="B")
    trade_df = pd.DataFrame({
        "Open": np.linspace(100, 109, 10),
        "High": np.linspace(101, 110, 10),
        "Low": np.linspace(99, 108, 10),
        "Close": np.linspace(100.5, 109.5, 10),
        "Volume": np.full(10, 1_000_000.0),
    }, index=trade_idx)
    trade = trade_engine.simulate_trade(ticker="SYNH", ohlcv=trade_df, as_of=trade_df.index[4].date())
    assert trade.hold_days == 2
    print("trade hold_days trading-day count ok", trade.hold_days)

    print("=== PASS ===")


if __name__ == "__main__":
    main()
