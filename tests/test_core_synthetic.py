from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from phoenix_core import bootstrap
from phoenix_core.config import load_config
from phoenix_core.default_features import BASELINE_FEATURE_NAMES
from phoenix_core.engines.feature_engine import CatalogFeatureEngine
from phoenix_core.labels import compute_forward_labels
from phoenix_core.models import (
    ContextEngineInput,
    DecisionInput,
    FeatureEngineInput,
    PatternEngineInput,
    SimilarityQuery,
)
from phoenix_core.pipeline import build_pattern_records
from phoenix_core.registry import EngineRegistry


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
    print("similarity ok", sim.n_similar, sim.hit_rate_5d)

    context_engine = EngineRegistry.get("context_engine", "market_v1")
    ctx = context_engine.run(ContextEngineInput(as_of=fv.as_of, market_ohlcv=raw, sector_etf="SOXX"))
    assert 0 <= ctx.market_score <= 100

    decision_engine = EngineRegistry.get("decision_engine", "weighted_v1")
    decision = decision_engine.run(DecisionInput(target, fv.as_of, pattern, sim, ctx, fv))
    assert 0 <= decision.suitability_score <= 100
    assert 0 <= decision.confidence_score <= 100
    explanation = EngineRegistry.get("explain_engine", "template_v1").run(decision)
    assert "과거 유사 사례" in explanation
    print("decision/explain ok", decision.suitability_score, explanation)

    backtest = EngineRegistry.get("backtest_engine", "as_of_v1")
    result = backtest.evaluate(records, decision_fn=lambda r: r.feature_vector.values["ret_20d"] > 0.05)
    assert result.n_records > 0
    print("backtest ok", result.n_trades, result.hit_rate)

    print("=== PASS ===")


if __name__ == "__main__":
    main()
