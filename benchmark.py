from __future__ import annotations

import argparse
import logging
import math
import os
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

from phoenix_core import bootstrap
from phoenix_core.config import AppConfig, load_config
from phoenix_core.data_loader import download_ohlcv
from phoenix_core.default_features import BASELINE_FEATURE_NAMES
from phoenix_core.models import FeatureEngineInput
from phoenix_core.pipeline import analyze_ticker_quiet, build_pattern_records
from phoenix_core.registry import EngineRegistry
from phoenix_core.engines.statistical_validation_engine import StatisticalValidationEngine, ValidationConfig
from phoenix_core.trade import (
    EntryMode,
    SameDayRule,
    TradeCandidate,
    TradeConfig,
    TradeSimulationEngine,
)


@dataclass
class BenchmarkConfig:
    start: str
    end: str
    top_n: int
    period: str
    frequency: str
    max_dates: Optional[int]
    refresh: bool
    retrain: bool
    k: Optional[int]
    min_train_records: int
    random_baseline: int
    top_list: Optional[str]
    random_seed: int
    trade_sim: bool
    take_profit: float
    stop_loss: float
    hold_days: int
    same_day_rule: str
    entry_mode: str
    fee_bps: float
    slippage_bps: float
    grid_search: bool
    tp_list: Optional[str]
    sl_list: Optional[str]
    hold_list: Optional[str]
    bootstrap: int
    confidence_level: float
    drop_incomplete_future: bool
    min_dollar_volume: float
    min_price: float
    max_gap_open: Optional[float]
    entry_penalty_bps: float
    resume_dir: Optional[str]
    train_test: bool
    train_start: Optional[str]
    train_end: Optional[str]
    test_start: Optional[str]
    test_end: Optional[str]
    embargo_trading_days: int
    train_top_k_rules: int
    rank_mode: str
    xgb_blend_weights: List[float]


def _ensure_dirs(config: AppConfig) -> None:
    os.makedirs(config.cache_dir, exist_ok=True)
    os.makedirs(config.models_dir, exist_ok=True)
    os.makedirs(config.reports_dir, exist_ok=True)


def _pct(x: float | int | None, digits: int = 1) -> str:
    if x is None or not np.isfinite(float(x)):
        return "n/a"
    return f"{float(x) * 100:.{digits}f}%"


def _fmt(x: float | int | None, digits: int = 2) -> str:
    if x is None or not np.isfinite(float(x)):
        return "n/a"
    return f"{float(x):.{digits}f}"


def _max_drawdown(returns: Iterable[float]) -> float:
    arr = np.array([float(r) for r in returns if pd.notna(r)], dtype=float)
    if len(arr) == 0:
        return 0.0
    equity = np.cumprod(1.0 + arr)
    peak = np.maximum.accumulate(equity)
    dd = (peak - equity) / np.maximum(peak, 1e-12)
    return float(np.max(dd)) if len(dd) else 0.0


def _profit_factor(returns: Iterable[float]) -> float:
    arr = np.array([float(r) for r in returns if pd.notna(r)], dtype=float)
    if len(arr) == 0:
        return 0.0
    gains = arr[arr > 0].sum()
    losses = -arr[arr < 0].sum()
    if losses <= 1e-12:
        return 999.0 if gains > 0 else 0.0
    return float(gains / losses)


def _sharpe(returns: Iterable[float]) -> float:
    arr = np.array([float(r) for r in returns if pd.notna(r)], dtype=float)
    if len(arr) < 2:
        return 0.0
    std = arr.std(ddof=0)
    return float(arr.mean() / std) if std > 1e-12 else 0.0


def _select_asof_dates(spy: pd.DataFrame, start: str, end: str, frequency: str, max_dates: Optional[int]) -> List[pd.Timestamp]:
    idx = pd.DatetimeIndex(spy.index).sort_values()
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    idx = idx[(idx >= start_ts) & (idx <= end_ts)]
    if idx.empty:
        raise RuntimeError(f"벤치마크 기준일이 없습니다: {start} ~ {end}")

    frequency = frequency.lower()
    if frequency == "daily":
        dates = list(idx)
    elif frequency == "weekly":
        # 각 주의 첫 거래일
        s = pd.Series(idx, index=idx)
        dates = list(s.groupby(idx.to_period("W")).first())
    elif frequency == "monthly":
        # 각 월의 첫 거래일
        s = pd.Series(idx, index=idx)
        dates = list(s.groupby(idx.to_period("M")).first())
    else:
        raise ValueError("--frequency 는 daily, weekly, monthly 중 하나여야 합니다.")

    dates = [pd.Timestamp(d) for d in dates]
    if max_dates and max_dates > 0 and len(dates) > max_dates:
        # 기간 전체에 골고루 분포하도록 균등 샘플링
        positions = np.linspace(0, len(dates) - 1, max_dates).round().astype(int)
        dates = [dates[i] for i in sorted(set(positions))]
    return dates


def _slice_raw_until(raw_data: Dict[str, pd.DataFrame], as_of: pd.Timestamp) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}
    for ticker, df in raw_data.items():
        hist = df[df.index <= as_of].copy()
        if len(hist) >= 80:
            out[ticker] = hist
    return out


def _build_prebuilt_for_asof(app_config: AppConfig, train_raw: Dict[str, pd.DataFrame], retrain: bool, k: int) -> Dict[str, Any]:
    feature_engine = EngineRegistry.get(
        "feature_engine",
        app_config.engines.get("feature_engine", "catalog_v1"),
        feature_names=BASELINE_FEATURE_NAMES,
    )
    records = build_pattern_records(train_raw, feature_engine, BASELINE_FEATURE_NAMES)
    if len(records) < 100:
        raise RuntimeError(f"PatternRecord가 너무 적습니다: {len(records)}")

    pattern_engine = EngineRegistry.get(
        "pattern_engine",
        app_config.engines.get("pattern_engine", "isolation_forest"),
        feature_names=BASELINE_FEATURE_NAMES,
    )
    pattern_engine.fit(records)

    similarity_engine = EngineRegistry.get(
        "similarity_engine",
        app_config.engines.get("similarity_engine", "cosine_knn"),
        feature_names=BASELINE_FEATURE_NAMES,
        k=k,
    )
    similarity_engine.build(records)

    return {
        "raw_data": train_raw,
        "feature_engine": feature_engine,
        "records": records,
        "pattern_engine": pattern_engine,
        "similarity_engine": similarity_engine,
    }


def _future_result(full_raw: Dict[str, pd.DataFrame], ticker: str, as_of_date, horizons=(5, 10)) -> Dict[str, float]:
    ticker = ticker.upper()
    if ticker not in full_raw:
        return {}
    df = full_raw[ticker].sort_index()
    ts = pd.Timestamp(as_of_date)
    if ts not in df.index:
        # as_of가 휴장일이면 직전 거래일을 사용한다.
        prior = df.index[df.index <= ts]
        if len(prior) == 0:
            return {}
        ts = pd.Timestamp(prior[-1])
    loc = df.index.get_loc(ts)
    if isinstance(loc, slice):
        loc = loc.start
    if not isinstance(loc, (int, np.integer)):
        loc = int(loc[0])
    close = float(df.iloc[loc]["Close"])
    result: Dict[str, float] = {}
    for h in horizons:
        fut = df.iloc[loc + 1: loc + 1 + h]
        if len(fut) < h or close <= 0:
            result[f"fwd_max_ret_{h}d"] = np.nan
            result[f"fwd_close_ret_{h}d"] = np.nan
            result[f"fwd_min_ret_{h}d"] = np.nan
            continue
        max_high = float(fut["High"].max())
        min_low = float(fut["Low"].min())
        exit_close = float(fut.iloc[-1]["Close"])
        result[f"fwd_max_ret_{h}d"] = (max_high - close) / close
        result[f"fwd_close_ret_{h}d"] = (exit_close - close) / close
        result[f"fwd_min_ret_{h}d"] = (min_low - close) / close
    result["hit_5pct_5d"] = 1.0 if result.get("fwd_max_ret_5d", np.nan) >= 0.05 else (np.nan if pd.isna(result.get("fwd_max_ret_5d", np.nan)) else 0.0)
    result["hit_10pct_10d"] = 1.0 if result.get("fwd_max_ret_10d", np.nan) >= 0.10 else (np.nan if pd.isna(result.get("fwd_max_ret_10d", np.nan)) else 0.0)
    result["close_hit_5pct_5d"] = 1.0 if result.get("fwd_close_ret_5d", np.nan) >= 0.05 else (np.nan if pd.isna(result.get("fwd_close_ret_5d", np.nan)) else 0.0)
    result["close_hit_10pct_10d"] = 1.0 if result.get("fwd_close_ret_10d", np.nan) >= 0.10 else (np.nan if pd.isna(result.get("fwd_close_ret_10d", np.nan)) else 0.0)
    return result


def _row_from_decision(
    rank: int,
    decision,
    meta: Dict[str, Any],
    full_raw: Dict[str, pd.DataFrame],
    *,
    xgb_score: float = float("nan"),
    xgb_blend_weight: float = 0.30,
) -> Dict[str, Any]:
    future = _future_result(full_raw, decision.ticker, decision.as_of)
    sector_rotation = meta.get("sector_rotation")
    target_strength = getattr(sector_rotation, "target_strength", None) if sector_rotation else None
    regime_result = meta.get("regime_result")
    xgb_score_available = bool(np.isfinite(float(xgb_score)))
    xgb_score_value = float(xgb_score) if xgb_score_available else 0.0
    final_rank_score = _blend_rank_score(
        decision.suitability_score,
        xgb_score_value,
        xgb_score_available=xgb_score_available,
        xgb_blend_weight=xgb_blend_weight,
    )
    return {
        "rank": rank,
        "rank_mode": "decision",
        "rank_score": float(decision.suitability_score),
        "xgb_blend_weight": float(xgb_blend_weight),
        "xgb_score": xgb_score_value,
        "xgb_score_available": xgb_score_available,
        "final_rank_score": final_rank_score,
        "as_of": str(decision.as_of),
        "ticker": decision.ticker,
        "suitability_score": decision.suitability_score,
        "confidence_score": decision.confidence_score,
        "risk_score": decision.risk_score,
        "label": decision.label,
        "market_score": decision.sub_scores.get("market_score", np.nan),
        "sector_score": decision.sub_scores.get("sector_rotation_score", np.nan),
        "sector_etf": meta.get("sector_etf"),
        "sector_return_5d": getattr(target_strength, "return_5d", np.nan),
        "sector_return_20d": getattr(target_strength, "return_20d", np.nan),
        "regime": getattr(regime_result, "regime", ""),
        "regime_confidence": getattr(regime_result, "confidence_score", np.nan),
        "pattern_rarity": decision.sub_scores.get("anomaly_percentile", np.nan),
        "hold_score": decision.sub_scores.get("hold_score", np.nan),
        "similarity_hit_5d": decision.success_rate_5d,
        "similarity_hit_10d": decision.success_rate_10d,
        "n_similar": decision.sub_scores.get("n_similar", np.nan),
        "avg_similarity": decision.sub_scores.get("avg_similarity", np.nan),
        "fwd_max_ret_5d": future.get("fwd_max_ret_5d", np.nan),
        "fwd_max_ret_10d": future.get("fwd_max_ret_10d", np.nan),
        "fwd_close_ret_5d": future.get("fwd_close_ret_5d", np.nan),
        "fwd_close_ret_10d": future.get("fwd_close_ret_10d", np.nan),
        "fwd_min_ret_5d": future.get("fwd_min_ret_5d", np.nan),
        "fwd_min_ret_10d": future.get("fwd_min_ret_10d", np.nan),
        "hit_5pct_5d": future.get("hit_5pct_5d", np.nan),
        "hit_10pct_10d": future.get("hit_10pct_10d", np.nan),
        "close_hit_5pct_5d": future.get("close_hit_5pct_5d", np.nan),
        "close_hit_10pct_10d": future.get("close_hit_10pct_10d", np.nan),
    }


def _parse_xgb_blend_weights(value: Optional[str | float]) -> List[float]:
    if value is None:
        return [0.30]
    if isinstance(value, (float, int)):
        raw_parts = [str(value)]
    else:
        raw_parts = [part.strip() for part in str(value).split(",") if part.strip()]
    if not raw_parts:
        return [0.30]
    weights: List[float] = []
    seen: set[float] = set()
    for part in raw_parts:
        weight = round(float(part), 6)
        if weight < 0.0 or weight > 1.0:
            raise ValueError("--xgb-blend-weight 값은 0.0~1.0 사이여야 합니다.")
        if weight in seen:
            continue
        seen.add(weight)
        weights.append(weight)
    return weights


def _primary_rank_mode(rank_mode: str) -> str:
    return "ranking" if str(rank_mode) == "both" else str(rank_mode)


def _primary_xgb_blend_weight(weights: Sequence[float]) -> float:
    values = [float(w) for w in weights]
    for weight in values:
        if abs(weight - 0.30) <= 1e-9:
            return 0.30
    return values[0] if values else 0.30


def _blend_rank_score(
    suitability_score: float,
    xgb_score: float,
    *,
    xgb_score_available: bool,
    xgb_blend_weight: float,
) -> float:
    suitability = float(suitability_score)
    weight = float(np.clip(float(xgb_blend_weight), 0.0, 1.0))
    if weight <= 0.0 or not xgb_score_available or not np.isfinite(float(xgb_score)):
        return suitability
    return (1.0 - weight) * suitability + weight * (float(xgb_score) * 100.0)


def _rank_candidate_rows(rows: List[Dict[str, Any]], rank_mode: str, xgb_blend_weight: float) -> List[Dict[str, Any]]:
    ranked: List[Dict[str, Any]] = []
    mode = str(rank_mode)
    weight = float(xgb_blend_weight)
    for row in rows:
        copied = dict(row)
        xgb_available = bool(copied.get("xgb_score_available", False))
        final_score = _blend_rank_score(
            float(copied.get("suitability_score", 0.0) or 0.0),
            float(copied.get("xgb_score", 0.0) or 0.0),
            xgb_score_available=xgb_available,
            xgb_blend_weight=weight,
        )
        copied["final_rank_score"] = final_score
        copied["xgb_blend_weight"] = weight
        copied["rank_mode"] = mode
        if mode == "ranking":
            copied["rank_score"] = final_score
        elif mode == "decision":
            copied["rank_score"] = float(copied.get("suitability_score", 0.0) or 0.0)
        else:
            raise ValueError(f"지원하지 않는 rank_mode: {rank_mode}")
        ranked.append(copied)

    ranked.sort(
        key=lambda r: (
            str(r.get("as_of")),
            -float(r.get("rank_score", 0.0) or 0.0),
            -float(r.get("suitability_score", 0.0) or 0.0),
            -float(r.get("confidence_score", 0.0) or 0.0),
            str(r.get("ticker", "")),
        )
    )

    out: List[Dict[str, Any]] = []
    current_as_of = None
    rank = 0
    for row in ranked:
        as_of = str(row.get("as_of"))
        if as_of != current_as_of:
            current_as_of = as_of
            rank = 1
        else:
            rank += 1
        copied = dict(row)
        copied["rank"] = rank
        out.append(copied)
    return out


def _score_decision_with_ranking_model(ranking_engine: Any, xgb_model: Any, prebuilt: Dict[str, Any], decision) -> float:
    if ranking_engine is None or xgb_model is None:
        return float("nan")
    feature_engine = prebuilt.get("feature_engine")
    raw_data = prebuilt.get("raw_data", {})
    if feature_engine is None or decision.ticker not in raw_data:
        return float("nan")
    feature_vector = feature_engine.run(
        FeatureEngineInput(ticker=decision.ticker, ohlcv=raw_data[decision.ticker], as_of=decision.as_of)
    )
    return float(ranking_engine._xgb_score(xgb_model, feature_vector.values))


def _rank_mode_comparison(
    candidate_rows: List[Dict[str, Any]],
    full_raw: Dict[str, pd.DataFrame],
    bench: BenchmarkConfig,
) -> pd.DataFrame:
    if not candidate_rows:
        return pd.DataFrame()

    trade_cache = _build_trade_outcome_cache(
        candidate_rows,
        full_raw,
        take_profit=bench.take_profit,
        stop_loss=bench.stop_loss,
        hold_days=bench.hold_days,
        same_day_rule=bench.same_day_rule,
        entry_mode=bench.entry_mode,
        fee_bps=bench.fee_bps,
        slippage_bps=bench.slippage_bps,
        min_dollar_volume=bench.min_dollar_volume,
        min_price=bench.min_price,
        max_gap_open=bench.max_gap_open,
        entry_penalty_bps=bench.entry_penalty_bps,
        top_n=bench.top_n,
    )
    random_dist = _random_trade_distribution(
        candidate_rows,
        trade_cache,
        top_n=bench.top_n,
        iterations=bench.random_baseline,
        seed=bench.random_seed,
    )
    random_values = (
        random_dist["portfolio_return_by_date_mean"].dropna().astype(float).values
        if not random_dist.empty and "portfolio_return_by_date_mean" in random_dist.columns
        else np.array([], dtype=float)
    )
    random_mean = float(np.mean(random_values)) if len(random_values) else np.nan
    stats_engine = StatisticalValidationEngine(
        ValidationConfig(
            bootstrap_iterations=int(bench.bootstrap),
            confidence_level=float(bench.confidence_level),
            random_seed=int(bench.random_seed),
        )
    )

    modes: List[tuple[str, float]] = [("decision", 0.0)]
    for weight in bench.xgb_blend_weights:
        modes.append(("ranking", float(weight)))

    seen: set[tuple[str, float]] = set()
    rows: List[Dict[str, Any]] = []
    for mode, weight in modes:
        key = (mode, round(float(weight), 6))
        if key in seen:
            continue
        seen.add(key)
        ranked_rows = _rank_candidate_rows(candidate_rows, mode, weight)
        selected = _rows_for_top_n(ranked_rows, bench.top_n)
        trade_rows, trade_summary = _build_trade_rows_with_engine_v20(
            selected,
            full_raw,
            take_profit=bench.take_profit,
            stop_loss=bench.stop_loss,
            hold_days=bench.hold_days,
            same_day_rule=bench.same_day_rule,
            entry_mode=bench.entry_mode,
            fee_bps=bench.fee_bps,
            slippage_bps=bench.slippage_bps,
            min_dollar_volume=bench.min_dollar_volume,
            min_price=bench.min_price,
            max_gap_open=bench.max_gap_open,
            entry_penalty_bps=bench.entry_penalty_bps,
            top_n=bench.top_n,
        )
        portfolio_mean = float(trade_summary.get("portfolio_return_by_date_mean", np.nan))
        p_value = stats_engine.empirical_p_value(portfolio_mean, random_values, higher_is_better=True) if len(random_values) and np.isfinite(portfolio_mean) else np.nan
        rows.append({
            "rank_mode": mode,
            "xgb_blend_weight": float(weight),
            "top_n": int(bench.top_n),
            "n_dates": int(pd.DataFrame(selected)["as_of"].nunique()) if selected else 0,
            "selected_slots": int(len(selected)),
            "portfolio_return_by_date_mean": portfolio_mean,
            "random_mean": random_mean,
            "alpha": portfolio_mean - random_mean if np.isfinite(portfolio_mean) and np.isfinite(random_mean) else np.nan,
            "p_value": p_value,
            "mdd": float(trade_summary.get("portfolio_mdd", np.nan)),
            "active_trades": int(trade_summary.get("n_active_trades", 0) or 0),
            "cash_slots": int(trade_summary.get("cash_slots", 0) or 0),
        })
    return pd.DataFrame(rows)


def _summarize_rows(rows: List[Dict[str, Any]], top_n: int) -> Dict[str, Any]:
    df = pd.DataFrame(rows)
    if df.empty:
        return {
            "n_dates": 0,
            "n_trades": 0,
            "top_n": top_n,
            "hit_5pct_5d_rate": 0.0,
            "hit_10pct_10d_rate": 0.0,
            "avg_fwd_max_ret_5d": 0.0,
            "avg_fwd_max_ret_10d": 0.0,
            "close_hit_5pct_5d_rate": 0.0,
            "close_hit_10pct_10d_rate": 0.0,
            "avg_fwd_close_ret_5d": 0.0,
            "avg_fwd_close_ret_10d": 0.0,
            "avg_fwd_min_ret_5d": 0.0,
            "avg_fwd_min_ret_10d": 0.0,
            "sharpe_5d": 0.0,
            "mdd_5d": 0.0,
            "profit_factor_5d": 0.0,
        }
    return {
        "n_dates": int(df["as_of"].nunique()),
        "n_trades": int(len(df)),
        "top_n": top_n,
        "hit_5pct_5d_rate": float(pd.to_numeric(df["hit_5pct_5d"], errors="coerce").mean()),
        "hit_10pct_10d_rate": float(pd.to_numeric(df["hit_10pct_10d"], errors="coerce").mean()),
        "avg_fwd_max_ret_5d": float(pd.to_numeric(df["fwd_max_ret_5d"], errors="coerce").mean()),
        "avg_fwd_max_ret_10d": float(pd.to_numeric(df["fwd_max_ret_10d"], errors="coerce").mean()),
        "close_hit_5pct_5d_rate": float(pd.to_numeric(df["close_hit_5pct_5d"], errors="coerce").mean()),
        "close_hit_10pct_10d_rate": float(pd.to_numeric(df["close_hit_10pct_10d"], errors="coerce").mean()),
        "avg_fwd_close_ret_5d": float(pd.to_numeric(df["fwd_close_ret_5d"], errors="coerce").mean()),
        "avg_fwd_close_ret_10d": float(pd.to_numeric(df["fwd_close_ret_10d"], errors="coerce").mean()),
        "avg_fwd_min_ret_5d": float(pd.to_numeric(df["fwd_min_ret_5d"], errors="coerce").mean()),
        "avg_fwd_min_ret_10d": float(pd.to_numeric(df["fwd_min_ret_10d"], errors="coerce").mean()),
        "sharpe_5d": _sharpe(pd.to_numeric(df["fwd_max_ret_5d"], errors="coerce")),
        "mdd_5d": _max_drawdown(pd.to_numeric(df["fwd_max_ret_5d"], errors="coerce")),
        "profit_factor_5d": _profit_factor(pd.to_numeric(df["fwd_max_ret_5d"], errors="coerce")),
        "avg_suitability": float(pd.to_numeric(df["suitability_score"], errors="coerce").mean()),
        "avg_confidence": float(pd.to_numeric(df["confidence_score"], errors="coerce").mean()),
        "avg_risk": float(pd.to_numeric(df["risk_score"], errors="coerce").mean()),
    }




def _parse_top_list(top_list: Optional[str], top_n: int) -> List[int]:
    if not top_list:
        return [int(top_n)]
    values: List[int] = []
    for part in top_list.split(","):
        part = part.strip()
        if not part:
            continue
        n = int(part)
        if n <= 0:
            raise ValueError("--top-list 값은 양수여야 합니다.")
        values.append(n)
    if not values:
        return [int(top_n)]
    return sorted(set(values))


def _rows_for_top_n(ranked_rows: List[Dict[str, Any]], top_n: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for row in ranked_rows:
        by_date.setdefault(str(row["as_of"]), []).append(row)
    for _as_of, rows in by_date.items():
        rows = sorted(rows, key=lambda r: int(r.get("rank", 999999)))[:top_n]
        for i, row in enumerate(rows, start=1):
            copied = dict(row)
            copied["rank"] = i
            copied["top_n_eval"] = top_n
            out.append(copied)
    return out


def _top_list_summary(ranked_rows: List[Dict[str, Any]], top_values: Sequence[int]) -> pd.DataFrame:
    rows = []
    for n in top_values:
        subset = _rows_for_top_n(ranked_rows, int(n))
        summary = _summarize_rows(subset, int(n))
        summary["top_n_eval"] = int(n)
        rows.append(summary)
    return pd.DataFrame(rows)


def _random_baseline_summary(
    all_candidate_rows: List[Dict[str, Any]],
    top_values: Sequence[int],
    iterations: int,
    seed: int,
) -> pd.DataFrame:
    if iterations <= 0 or not all_candidate_rows:
        return pd.DataFrame()
    rng = np.random.default_rng(seed)
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for row in all_candidate_rows:
        by_date.setdefault(str(row["as_of"]), []).append(row)

    out = []
    for top_n in top_values:
        metrics = []
        for _ in range(iterations):
            sampled: List[Dict[str, Any]] = []
            for _as_of, rows in by_date.items():
                if not rows:
                    continue
                size = min(int(top_n), len(rows))
                idx = rng.choice(len(rows), size=size, replace=False)
                for rank, j in enumerate(idx, start=1):
                    copied = dict(rows[int(j)])
                    copied["rank"] = rank
                    sampled.append(copied)
            metrics.append(_summarize_rows(sampled, int(top_n)))
        mdf = pd.DataFrame(metrics)
        if mdf.empty:
            continue
        row = {
            "top_n_eval": int(top_n),
            "iterations": int(iterations),
            "random_hit_5pct_5d_mean": float(mdf["hit_5pct_5d_rate"].mean()),
            "random_hit_5pct_5d_std": float(mdf["hit_5pct_5d_rate"].std(ddof=0)),
            "random_hit_10pct_10d_mean": float(mdf["hit_10pct_10d_rate"].mean()),
            "random_hit_10pct_10d_std": float(mdf["hit_10pct_10d_rate"].std(ddof=0)),
            "random_avg_fwd5_mean": float(mdf["avg_fwd_max_ret_5d"].mean()),
            "random_avg_fwd10_mean": float(mdf["avg_fwd_max_ret_10d"].mean()),
            "random_close_hit_5pct_5d_mean": float(mdf["close_hit_5pct_5d_rate"].mean()),
            "random_close_hit_5pct_5d_std": float(mdf["close_hit_5pct_5d_rate"].std(ddof=0)),
            "random_close_hit_10pct_10d_mean": float(mdf["close_hit_10pct_10d_rate"].mean()),
            "random_close_hit_10pct_10d_std": float(mdf["close_hit_10pct_10d_rate"].std(ddof=0)),
            "random_avg_fwd_close5_mean": float(mdf["avg_fwd_close_ret_5d"].mean()),
            "random_avg_fwd_close10_mean": float(mdf["avg_fwd_close_ret_10d"].mean()),
            "random_avg_fwd_min5_mean": float(mdf["avg_fwd_min_ret_5d"].mean()),
            "random_avg_fwd_min10_mean": float(mdf["avg_fwd_min_ret_10d"].mean()),
            "random_sharpe_5d_mean": float(mdf["sharpe_5d"].mean()),
            "random_mdd_5d_mean": float(mdf["mdd_5d"].mean()),
            "random_profit_factor_5d_mean": float(mdf["profit_factor_5d"].replace(999.0, np.nan).mean()),
        }
        out.append(row)
    return pd.DataFrame(out)


def _merge_alpha_summary(top_summary: pd.DataFrame, random_summary: pd.DataFrame) -> pd.DataFrame:
    if top_summary.empty:
        return pd.DataFrame()
    out = top_summary.copy()
    if not random_summary.empty:
        out = out.merge(random_summary, on="top_n_eval", how="left")
        out["alpha_hit_5pct_5d"] = out["hit_5pct_5d_rate"] - out["random_hit_5pct_5d_mean"]
        out["alpha_hit_10pct_10d"] = out["hit_10pct_10d_rate"] - out["random_hit_10pct_10d_mean"]
        out["alpha_avg_fwd5"] = out["avg_fwd_max_ret_5d"] - out["random_avg_fwd5_mean"]
        out["alpha_avg_fwd10"] = out["avg_fwd_max_ret_10d"] - out["random_avg_fwd10_mean"]
        out["alpha_close_hit_5pct_5d"] = out["close_hit_5pct_5d_rate"] - out["random_close_hit_5pct_5d_mean"]
        out["alpha_close_hit_10pct_10d"] = out["close_hit_10pct_10d_rate"] - out["random_close_hit_10pct_10d_mean"]
        out["alpha_avg_fwd_close5"] = out["avg_fwd_close_ret_5d"] - out["random_avg_fwd_close5_mean"]
        out["alpha_avg_fwd_close10"] = out["avg_fwd_close_ret_10d"] - out["random_avg_fwd_close10_mean"]
        out["alpha_avg_fwd_min5"] = out["avg_fwd_min_ret_5d"] - out["random_avg_fwd_min5_mean"]
        out["alpha_avg_fwd_min10"] = out["avg_fwd_min_ret_10d"] - out["random_avg_fwd_min10_mean"]
    return out



def _build_trade_candidates(selected_rows: List[Dict[str, Any]]) -> List[TradeCandidate]:
    candidates: List[TradeCandidate] = []
    for row in selected_rows:
        metadata = {
            "label": row.get("label"),
            "suitability_score": row.get("suitability_score"),
            "confidence_score": row.get("confidence_score"),
            "risk_score": row.get("risk_score"),
            "market_score": row.get("market_score"),
            "sector_score": row.get("sector_score"),
            "sector_etf": row.get("sector_etf"),
            "pattern_rarity": row.get("pattern_rarity"),
            "similarity_hit_5d": row.get("similarity_hit_5d"),
            "similarity_hit_10d": row.get("similarity_hit_10d"),
        }
        candidates.append(
            TradeCandidate(
                ticker=str(row.get("ticker", "")).upper(),
                as_of=pd.Timestamp(row.get("as_of")).date(),
                score=float(row.get("suitability_score", 0.0) or 0.0),
                rank=int(row.get("rank", 0) or 0),
                metadata=metadata,
            )
        )
    return candidates


def _build_trade_rows_with_engine(
    selected_rows: List[Dict[str, Any]],
    full_raw: Dict[str, pd.DataFrame],
    take_profit: float,
    stop_loss: float,
    hold_days: int,
    same_day_rule: str,
    entry_mode: str,
    fee_bps: float,
    slippage_bps: float,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    config = TradeConfig(
        take_profit=float(take_profit),
        stop_loss=float(stop_loss),
        max_hold_days=int(hold_days),
        trailing_stop=None,
        entry_mode=EntryMode(entry_mode),
        same_day_rule=SameDayRule(same_day_rule),
        fee_bps=float(fee_bps),
        slippage_bps=float(slippage_bps),
    )
    engine = TradeSimulationEngine(config)
    candidates = _build_trade_candidates(selected_rows)
    results = engine.simulate_candidates(candidates=candidates, raw_data=full_raw, config=config)
    summary = engine.summarize(results).to_dict()
    summary.update({
        "take_profit": float(take_profit),
        "stop_loss": float(stop_loss),
        "hold_days": int(hold_days),
        "same_day_rule": str(same_day_rule),
        "engine": "TradeSimulationEngine",
        "entry_mode": config.entry_mode.value,
        "fee_bps": config.fee_bps,
        "slippage_bps": config.slippage_bps,
    })

    result_by_key = {(r.ticker, str(r.as_of), int(r.rank)): r.to_dict() for r in results}
    rows: List[Dict[str, Any]] = []
    for row in selected_rows:
        key = (
            str(row.get("ticker", "")).upper(),
            str(pd.Timestamp(row.get("as_of")).date()),
            int(row.get("rank", 0) or 0),
        )
        merged = dict(row)
        trade = result_by_key.get(key, {})
        if trade:
            merged.update(trade)
            # v1.5/v1.8 호환 컬럼
            merged["trade_return"] = trade.get("net_return", np.nan)
            merged["exit_reason"] = trade.get("exit_reason", "")
            merged["holding_days"] = trade.get("hold_days", np.nan)
        rows.append(merged)
    return rows, summary


def _parse_float_list(value: Optional[str], default: List[float]) -> List[float]:
    if not value:
        return default
    out: List[float] = []
    for part in value.split(","):
        part = part.strip()
        if part:
            out.append(float(part))
    return sorted(set(out)) if out else default


def _parse_int_list(value: Optional[str], default: List[int]) -> List[int]:
    if not value:
        return default
    out: List[int] = []
    for part in value.split(","):
        part = part.strip()
        if part:
            out.append(int(part))
    return sorted(set(out)) if out else default


def _required_future_days(bench: BenchmarkConfig) -> int:
    required = max(10, int(bench.hold_days))
    if bench.hold_list:
        try:
            required = max(required, max(_parse_int_list(bench.hold_list, [required])))
        except Exception:
            pass
    return required


def _has_full_future_window(full_raw: Dict[str, pd.DataFrame], ticker: str, as_of_date, required_days: int) -> bool:
    ticker = str(ticker).upper()
    if ticker not in full_raw:
        return False
    df = full_raw[ticker].sort_index()
    ts = pd.Timestamp(as_of_date)
    if ts not in df.index:
        prior = df.index[df.index <= ts]
        if len(prior) == 0:
            return False
        ts = pd.Timestamp(prior[-1])
    loc = df.index.get_loc(ts)
    if isinstance(loc, slice):
        loc = loc.start
    if not isinstance(loc, (int, np.integer)):
        loc = int(loc[0])
    return len(df.iloc[loc + 1: loc + 1 + int(required_days)]) >= int(required_days)


def _filter_complete_future_rows(rows: List[Dict[str, Any]], full_raw: Dict[str, pd.DataFrame], required_days: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in rows:
        if _has_full_future_window(full_raw, row.get("ticker"), row.get("as_of"), required_days):
            out.append(row)
    return out


def _is_tradable_row(
    row: Dict[str, Any],
    full_raw: Dict[str, pd.DataFrame],
    *,
    entry_mode: str,
    min_dollar_volume: float = 0.0,
) -> bool:
    """as_of 기준 실제 거래 가능한 후보만 남긴다.

    yfinance 기반 MVP에서 생존편향을 완전히 제거할 수는 없지만,
    적어도 해당 as_of에 OHLCV/거래량/진입가가 유효하지 않은 종목은 random pool에서 제거한다.
    """
    ticker = str(row.get("ticker", "")).upper()
    if ticker not in full_raw:
        return False
    df = full_raw[ticker].sort_index()
    if df.empty:
        return False

    ts = pd.Timestamp(row.get("as_of"))
    if ts not in df.index:
        prior = df.index[df.index <= ts]
        if len(prior) == 0:
            return False
        ts = pd.Timestamp(prior[-1])

    loc = df.index.get_loc(ts)
    if isinstance(loc, slice):
        loc = loc.start
    if not isinstance(loc, (int, np.integer)):
        loc = int(loc[0])

    cur = df.iloc[int(loc)]
    close = float(cur.get("Close", np.nan))
    volume = float(cur.get("Volume", np.nan)) if "Volume" in df.columns else np.nan
    if not np.isfinite(close) or close <= 0:
        return False
    if not np.isfinite(volume) or volume <= 0:
        return False
    if float(min_dollar_volume or 0.0) > 0 and close * volume < float(min_dollar_volume):
        return False

    if str(entry_mode) == "next_open":
        if int(loc) + 1 >= len(df):
            return False
        nxt = df.iloc[int(loc) + 1]
        nxt_open = float(nxt.get("Open", np.nan))
        if not np.isfinite(nxt_open) or nxt_open <= 0:
            return False
    return True


def _filter_tradable_rows(
    rows: List[Dict[str, Any]],
    full_raw: Dict[str, pd.DataFrame],
    *,
    entry_mode: str,
    min_dollar_volume: float = 0.0,
) -> List[Dict[str, Any]]:
    return [
        row for row in rows
        if _is_tradable_row(row, full_raw, entry_mode=entry_mode, min_dollar_volume=min_dollar_volume)
    ]



def _execution_filter_info(
    row: Dict[str, Any],
    full_raw: Dict[str, pd.DataFrame],
    *,
    entry_mode: str,
    min_dollar_volume: float = 0.0,
    min_price: float = 0.0,
    max_gap_open: Optional[float] = None,
) -> Dict[str, Any]:
    """as_of/entry 시점 기준 실행 가능 여부를 판단한다.

    원칙:
    - min_price, min_dollar_volume: as_of close/volume 기준
    - max_gap_open: next_open / as_of close - 1 기준. long 기준 gap-up 과열 진입 방지.
    - 필터 실패는 종목 교체가 아니라 cash slot으로 처리한다.
    """
    ticker = str(row.get("ticker", "")).upper()
    info: Dict[str, Any] = {
        "is_trade_eligible": False,
        "filter_reason": "unknown",
        "asof_close": np.nan,
        "asof_volume": np.nan,
        "asof_dollar_volume": np.nan,
        "entry_open": np.nan,
        "gap_open_return": np.nan,
    }
    if ticker not in full_raw:
        info["filter_reason"] = "missing_ohlcv"
        return info
    df = full_raw[ticker].sort_index()
    if df.empty:
        info["filter_reason"] = "empty_ohlcv"
        return info

    ts = pd.Timestamp(row.get("as_of"))
    if ts not in df.index:
        prior = df.index[df.index <= ts]
        if len(prior) == 0:
            info["filter_reason"] = "no_asof_bar"
            return info
        ts = pd.Timestamp(prior[-1])

    loc = df.index.get_loc(ts)
    if isinstance(loc, slice):
        loc = loc.start
    if not isinstance(loc, (int, np.integer)):
        loc = int(loc[0])
    loc = int(loc)

    cur = df.iloc[loc]
    close = float(cur.get("Close", np.nan))
    volume = float(cur.get("Volume", np.nan)) if "Volume" in df.columns else np.nan
    info["asof_close"] = close
    info["asof_volume"] = volume
    info["asof_dollar_volume"] = close * volume if np.isfinite(close) and np.isfinite(volume) else np.nan

    if not np.isfinite(close) or close <= 0:
        info["filter_reason"] = "invalid_asof_close"
        return info
    if float(min_price or 0.0) > 0 and close < float(min_price):
        info["filter_reason"] = "filtered_by_price"
        return info
    if not np.isfinite(volume) or volume <= 0:
        info["filter_reason"] = "invalid_asof_volume"
        return info
    if float(min_dollar_volume or 0.0) > 0 and close * volume < float(min_dollar_volume):
        info["filter_reason"] = "filtered_by_liquidity"
        return info

    if str(entry_mode) == "next_open":
        if loc + 1 >= len(df):
            info["filter_reason"] = "missing_entry_open"
            return info
        nxt_open = float(df.iloc[loc + 1].get("Open", np.nan))
        info["entry_open"] = nxt_open
        if not np.isfinite(nxt_open) or nxt_open <= 0:
            info["filter_reason"] = "invalid_entry_open"
            return info
        gap = (nxt_open / close) - 1.0
        info["gap_open_return"] = gap
        if max_gap_open is not None and float(max_gap_open) >= 0 and gap > float(max_gap_open):
            info["filter_reason"] = "filtered_by_gap"
            return info

    info["is_trade_eligible"] = True
    info["filter_reason"] = "eligible"
    return info


def _cash_slot_row(row: Dict[str, Any], info: Dict[str, Any], *, top_n: int) -> Dict[str, Any]:
    out = dict(row)
    out.update(info)
    out.update({
        "entry_date": pd.NaT,
        "exit_date": pd.NaT,
        "entry_price": np.nan,
        "exit_price": np.nan,
        "gross_return": 0.0,
        "net_return": 0.0,
        "trade_return": 0.0,
        "slot_return": 0.0,
        "hold_days": 0,
        "holding_days": 0,
        "exit_reason": "CASH",
        "is_cash_slot": True,
        "is_active_trade": False,
        "intended_slots": int(top_n),
        "entry_penalty_bps": 0.0,
    })
    return out


def _apply_entry_penalty_to_trade_row(row: Dict[str, Any], entry_penalty_bps: float) -> Dict[str, Any]:
    out = dict(row)
    penalty = float(entry_penalty_bps or 0.0) / 10000.0
    ret = pd.to_numeric(pd.Series([out.get("trade_return", out.get("net_return"))]), errors="coerce").iloc[0]
    if pd.notna(ret) and np.isfinite(float(ret)) and penalty > 0:
        new_ret = float(ret) - penalty
        out["net_return_before_entry_penalty"] = float(ret)
        out["net_return"] = new_ret
        out["trade_return"] = new_ret
        out["slot_return"] = new_ret
    else:
        out["slot_return"] = float(ret) if pd.notna(ret) and np.isfinite(float(ret)) else np.nan
    out["entry_penalty_bps"] = float(entry_penalty_bps or 0.0)
    out["is_cash_slot"] = False
    out["is_active_trade"] = True
    return out


def _summarize_trade_slot_rows(rows: List[Dict[str, Any]], top_n: int) -> Dict[str, Any]:
    df = pd.DataFrame(rows)
    if df.empty or "trade_return" not in df.columns:
        return {}
    df = df.copy()
    df["trade_return"] = pd.to_numeric(df["trade_return"], errors="coerce").fillna(0.0)
    active = df[df.get("is_active_trade", False) == True] if "is_active_trade" in df.columns else df
    port = _portfolio_returns_by_date(df.to_dict("records"), top_n=top_n)
    return {
        "n_slots": int(len(df)),
        "n_active_trades": int(len(active)),
        "cash_slots": int((df.get("is_cash_slot", False) == True).sum()) if "is_cash_slot" in df.columns else 0,
        "cash_weight_mean": float(port["cash_weight"].mean()) if not port.empty and "cash_weight" in port.columns else 0.0,
        "slot_avg_return": float(df["trade_return"].mean()),
        "slot_median_return": float(df["trade_return"].median()),
        "active_avg_return": float(pd.to_numeric(active["trade_return"], errors="coerce").mean()) if not active.empty else np.nan,
        "portfolio_return_by_date_mean": float(port["portfolio_return"].mean()) if not port.empty else np.nan,
        "portfolio_return_by_date_median": float(port["portfolio_return"].median()) if not port.empty else np.nan,
        "portfolio_positive_date_rate": float((port["portfolio_return"] > 0).mean()) if not port.empty else np.nan,
        "portfolio_mdd": _max_drawdown(port["portfolio_return"]) if not port.empty else np.nan,
    }


def _build_trade_rows_with_engine_v20(
    selected_rows: List[Dict[str, Any]],
    full_raw: Dict[str, pd.DataFrame],
    take_profit: float,
    stop_loss: float,
    hold_days: int,
    same_day_rule: str,
    entry_mode: str,
    fee_bps: float,
    slippage_bps: float,
    *,
    min_dollar_volume: float = 0.0,
    min_price: float = 0.0,
    max_gap_open: Optional[float] = None,
    entry_penalty_bps: float = 0.0,
    top_n: int = 10,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    active_rows: List[Dict[str, Any]] = []
    cash_rows: List[Dict[str, Any]] = []
    filter_counts: Dict[str, int] = {}

    for row in selected_rows:
        info = _execution_filter_info(
            row,
            full_raw,
            entry_mode=entry_mode,
            min_dollar_volume=min_dollar_volume,
            min_price=min_price,
            max_gap_open=max_gap_open,
        )
        reason = str(info.get("filter_reason", "unknown"))
        filter_counts[reason] = filter_counts.get(reason, 0) + 1
        if info.get("is_trade_eligible"):
            active = dict(row)
            active.update(info)
            active_rows.append(active)
        else:
            cash_rows.append(_cash_slot_row(row, info, top_n=top_n))

    active_trade_rows: List[Dict[str, Any]] = []
    engine_summary: Dict[str, Any] = {}
    if active_rows:
        active_trade_rows, engine_summary = _build_trade_rows_with_engine(
            active_rows,
            full_raw,
            take_profit=take_profit,
            stop_loss=stop_loss,
            hold_days=hold_days,
            same_day_rule=same_day_rule,
            entry_mode=entry_mode,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )
        active_trade_rows = [_apply_entry_penalty_to_trade_row(r, entry_penalty_bps) for r in active_trade_rows]

    rows = active_trade_rows + cash_rows
    rows = sorted(rows, key=lambda r: (str(r.get("as_of")), int(r.get("rank", 0) or 0), str(r.get("ticker", ""))))
    slot_summary = _summarize_trade_slot_rows(rows, top_n=top_n)
    summary = dict(engine_summary)
    summary.update(slot_summary)
    summary.update({
        "take_profit": float(take_profit),
        "stop_loss": float(stop_loss),
        "hold_days": int(hold_days),
        "same_day_rule": str(same_day_rule),
        "engine": "TradeSimulationEngine+CashSlotV20",
        "entry_mode": str(entry_mode),
        "fee_bps": float(fee_bps),
        "slippage_bps": float(slippage_bps),
        "entry_penalty_bps": float(entry_penalty_bps or 0.0),
        "min_dollar_volume": float(min_dollar_volume or 0.0),
        "min_price": float(min_price or 0.0),
        "max_gap_open": np.nan if max_gap_open is None else float(max_gap_open),
    })
    for reason, count in filter_counts.items():
        summary[f"count_{reason}"] = int(count)
    return rows, summary


def _subperiod_stability_from_trade_rows(trade_rows: List[Dict[str, Any]], top_n: int) -> Dict[str, Any]:
    port = _portfolio_returns_by_date(trade_rows, top_n=top_n)
    if port.empty:
        return {
            "train_subperiods": 0,
            "train_positive_subperiods": 0,
            "train_min_subperiod_return": np.nan,
            "train_subperiod_return_std": np.nan,
            "stability_score": 0.0,
        }
    port = port.copy()
    port["year"] = pd.to_datetime(port["as_of"]).dt.year.astype(str)
    yearly = port.groupby("year")["portfolio_return"].mean()
    positive = int((yearly > 0).sum())
    min_ret = float(yearly.min()) if len(yearly) else np.nan
    std = float(yearly.std(ddof=0)) if len(yearly) > 1 else 0.0
    stability_score = float(positive) - max(0.0, -min_ret) * 100.0 - std * 10.0
    out = {
        "train_subperiods": int(len(yearly)),
        "train_positive_subperiods": positive,
        "train_min_subperiod_return": min_ret,
        "train_subperiod_return_std": std,
        "stability_score": stability_score,
    }
    for year, value in yearly.items():
        out[f"train_return_{year}"] = float(value)
    return out


def _grid_search_trade_rules(
    selected_rows: List[Dict[str, Any]],
    full_raw: Dict[str, pd.DataFrame],
    tp_values: List[float],
    sl_values: List[float],
    hold_values: List[int],
    same_day_rule: str,
    entry_mode: str,
    fee_bps: float,
    slippage_bps: float,
    *,
    min_dollar_volume: float = 0.0,
    min_price: float = 0.0,
    max_gap_open: Optional[float] = None,
    entry_penalty_bps: float = 0.0,
    top_n: int = 10,
) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for tp in tp_values:
        for sl in sl_values:
            for hold in hold_values:
                _trade_rows, summary = _build_trade_rows_with_engine_v20(
                    selected_rows,
                    full_raw,
                    take_profit=tp,
                    stop_loss=sl,
                    hold_days=hold,
                    same_day_rule=same_day_rule,
                    entry_mode=entry_mode,
                    fee_bps=fee_bps,
                    slippage_bps=slippage_bps,
                    min_dollar_volume=min_dollar_volume,
                    min_price=min_price,
                    max_gap_open=max_gap_open,
                    entry_penalty_bps=entry_penalty_bps,
                    top_n=top_n,
                )
                summary.update(_subperiod_stability_from_trade_rows(_trade_rows, top_n=top_n))
                summary = dict(summary)
                summary["take_profit"] = tp
                summary["stop_loss"] = sl
                summary["hold_days"] = hold
                summary["grid_scope"] = "in_sample_exploratory"
                summary["score_pf_mdd"] = float(summary.get("profit_factor", 0.0)) / max(float(summary.get("mdd", 0.0)), 0.01)
                summary["score_avg_mdd"] = float(summary.get("avg_return", 0.0)) / max(float(summary.get("mdd", 0.0)), 0.01)
                rows.append(summary)

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values(["profit_factor", "portfolio_return_by_date_mean", "train_min_subperiod_return", "mdd"], ascending=[False, False, False, True]).reset_index(drop=True)
    df.insert(0, "grid_rank", range(1, len(df) + 1))
    return df



def _trade_cache_key(row: Dict[str, Any]) -> tuple[str, str]:
    return (str(pd.Timestamp(row.get("as_of")).date()), str(row.get("ticker", "")).upper())


def _build_trade_outcome_cache(
    candidate_rows: List[Dict[str, Any]],
    full_raw: Dict[str, pd.DataFrame],
    *,
    take_profit: float,
    stop_loss: float,
    hold_days: int,
    same_day_rule: str,
    entry_mode: str,
    fee_bps: float,
    slippage_bps: float,
    min_dollar_volume: float = 0.0,
    min_price: float = 0.0,
    max_gap_open: Optional[float] = None,
    entry_penalty_bps: float = 0.0,
    top_n: int = 10,
) -> Dict[tuple[str, str], Dict[str, Any]]:
    """Random trade baseline용 캐시.

    모든 후보의 trade outcome을 한 번만 계산해두고,
    random iteration에서는 (as_of, ticker) lookup만 수행한다.
    """
    if not candidate_rows:
        return {}
    trade_rows, _summary = _build_trade_rows_with_engine_v20(
        candidate_rows,
        full_raw,
        take_profit=take_profit,
        stop_loss=stop_loss,
        hold_days=hold_days,
        same_day_rule=same_day_rule,
        entry_mode=entry_mode,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        min_dollar_volume=min_dollar_volume,
        min_price=min_price,
        max_gap_open=max_gap_open,
        entry_penalty_bps=entry_penalty_bps,
        top_n=top_n,
    )
    cache: Dict[tuple[str, str], Dict[str, Any]] = {}
    for row in trade_rows:
        key = _trade_cache_key(row)
        ret = pd.to_numeric(pd.Series([row.get("trade_return")]), errors="coerce").iloc[0]
        if pd.notna(ret) and np.isfinite(float(ret)):
            cache[key] = row
    return cache


def _portfolio_returns_by_date(trade_rows: List[Dict[str, Any]], top_n: Optional[int] = None) -> pd.DataFrame:
    """as_of 날짜별 포트폴리오 슬롯 수익률.

    v2.0 원칙:
    - TopN 슬롯은 고정한다.
    - 필터로 진입하지 못한 슬롯은 CASH return 0으로 포함한다.
    - 포트폴리오 return은 active positions 평균이 아니라 intended slots 기준 평균이다.
    """
    df = pd.DataFrame(trade_rows)
    if df.empty or "trade_return" not in df.columns:
        return pd.DataFrame(columns=["as_of", "portfolio_return", "n_slots", "active_positions", "cash_slots", "cash_weight"])
    df = df.copy()
    df["trade_return"] = pd.to_numeric(df["trade_return"], errors="coerce").fillna(0.0)

    # v2.0.1 hotfix:
    # as_of가 str / datetime.date / Timestamp로 섞이면 pandas sort_values에서
    # TypeError: '<' not supported between instances of 'str' and 'datetime.date'가 발생한다.
    # 날짜 키는 여기서 전부 ISO 문자열(YYYY-MM-DD)로 통일한다.
    df["as_of"] = pd.to_datetime(df["as_of"], errors="coerce")
    df = df.dropna(subset=["as_of"])
    if df.empty:
        return pd.DataFrame(columns=["as_of", "portfolio_return", "n_slots", "active_positions", "cash_slots", "cash_weight"])
    df["as_of"] = df["as_of"].dt.strftime("%Y-%m-%d")

    rows: List[Dict[str, Any]] = []
    for as_of, g in df.groupby("as_of", sort=True):
        n_slots = int(top_n or len(g))
        # g에는 cash slot도 포함되어 있어야 한다. 그래도 안전하게 denominator는 top_n 고정.
        returns_sum = float(g["trade_return"].sum())
        active_positions = int((g.get("is_active_trade", False) == True).sum()) if "is_active_trade" in g.columns else int(len(g))
        cash_slots = max(0, n_slots - active_positions)
        if "is_cash_slot" in g.columns:
            cash_slots = int((g["is_cash_slot"] == True).sum())
        row = {
            "as_of": as_of,
            "portfolio_return": returns_sum / max(n_slots, 1),
            "n_slots": n_slots,
            "active_positions": active_positions,
            "cash_slots": cash_slots,
            "cash_weight": cash_slots / max(n_slots, 1),
            "filtered_by_gap_count": int((g.get("filter_reason", "") == "filtered_by_gap").sum()) if "filter_reason" in g.columns else 0,
            "filtered_by_liquidity_count": int((g.get("filter_reason", "") == "filtered_by_liquidity").sum()) if "filter_reason" in g.columns else 0,
            "filtered_by_price_count": int((g.get("filter_reason", "") == "filtered_by_price").sum()) if "filter_reason" in g.columns else 0,
        }
        rows.append(row)
    return pd.DataFrame(rows).sort_values("as_of")


def _random_trade_distribution(
    all_candidate_rows: List[Dict[str, Any]],
    trade_cache: Dict[tuple[str, str], Dict[str, Any]],
    top_n: int,
    iterations: int,
    seed: int,
) -> pd.DataFrame:
    """Trade Simulation용 random baseline distribution.

    주의: 여기서는 매 iteration마다 TradeSimulationEngine을 재실행하지 않는다.
    v1.9.2는 candidate trade outcome cache를 사용해서 속도 폭탄을 피한다.
    """
    if iterations <= 0 or not all_candidate_rows or not trade_cache:
        return pd.DataFrame()

    rng = np.random.default_rng(seed + 99173)
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for row in all_candidate_rows:
        key = _trade_cache_key(row)
        if key in trade_cache:
            by_date.setdefault(str(row["as_of"]), []).append(row)

    rows: List[Dict[str, Any]] = []
    for iteration in range(1, int(iterations) + 1):
        sampled_trade_rows: List[Dict[str, Any]] = []
        for _as_of, candidates in by_date.items():
            if not candidates:
                continue
            size = min(int(top_n), len(candidates))
            idx = rng.choice(len(candidates), size=size, replace=False)
            for rank, j in enumerate(idx, start=1):
                src = candidates[int(j)]
                trade = dict(trade_cache.get(_trade_cache_key(src), {}))
                if not trade:
                    continue
                trade["rank"] = rank
                trade["top_n_eval"] = int(top_n)
                sampled_trade_rows.append(trade)

        trade_df = pd.DataFrame(sampled_trade_rows)
        if trade_df.empty or "trade_return" not in trade_df.columns:
            continue
        trade_df["trade_return"] = pd.to_numeric(trade_df["trade_return"], errors="coerce")
        trade_df = trade_df.dropna(subset=["as_of", "trade_return"])
        port_df = _portfolio_returns_by_date(trade_df.to_dict("records"), top_n=top_n)

        row = {
            "iteration": iteration,
            "top_n_eval": int(top_n),
            "n_trades": int(len(trade_df)),
            "n_dates": int(trade_df["as_of"].nunique()) if not trade_df.empty else 0,
            "trade_return_mean": float(trade_df["trade_return"].mean()) if not trade_df.empty else np.nan,
            "trade_return_median": float(trade_df["trade_return"].median()) if not trade_df.empty else np.nan,
            "trade_win_rate": float((trade_df["trade_return"] > 0).mean()) if not trade_df.empty else np.nan,
            "portfolio_return_by_date_mean": float(port_df["portfolio_return"].mean()) if not port_df.empty else np.nan,
            "portfolio_return_by_date_mdd": _max_drawdown(port_df["portfolio_return"]) if not port_df.empty else np.nan,
            "portfolio_positive_date_rate": float((port_df["portfolio_return"] > 0).mean()) if not port_df.empty else np.nan,
        }
        rows.append(row)
    return pd.DataFrame(rows)



def _random_metric_distribution(
    all_candidate_rows: List[Dict[str, Any]],
    top_n: int,
    iterations: int,
    seed: int,
) -> pd.DataFrame:
    if iterations <= 0 or not all_candidate_rows:
        return pd.DataFrame()
    rng = np.random.default_rng(seed)
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for row in all_candidate_rows:
        by_date.setdefault(str(row["as_of"]), []).append(row)

    rows: List[Dict[str, Any]] = []
    for iteration in range(1, int(iterations) + 1):
        sampled: List[Dict[str, Any]] = []
        for _as_of, candidates in by_date.items():
            if not candidates:
                continue
            size = min(int(top_n), len(candidates))
            idx = rng.choice(len(candidates), size=size, replace=False)
            for rank, j in enumerate(idx, start=1):
                copied = dict(candidates[int(j)])
                copied["rank"] = rank
                sampled.append(copied)
        summary = _summarize_rows(sampled, int(top_n))
        summary["iteration"] = iteration
        summary["top_n_eval"] = int(top_n)
        rows.append(summary)
    return pd.DataFrame(rows)


def _run_statistical_validation(
    selected_rows: List[Dict[str, Any]],
    candidate_rows: List[Dict[str, Any]],
    trade_rows: List[Dict[str, Any]],
    *,
    top_n: int,
    random_iterations: int,
    random_seed: int,
    bootstrap_iterations: int,
    confidence_level: float,
    trade_random_dist: Optional[pd.DataFrame] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    engine = StatisticalValidationEngine(
        ValidationConfig(
            bootstrap_iterations=int(bootstrap_iterations),
            confidence_level=float(confidence_level),
            random_seed=int(random_seed),
        )
    )

    random_dist = _random_metric_distribution(
        candidate_rows,
        top_n=int(top_n),
        iterations=int(random_iterations),
        seed=int(random_seed),
    )

    selected_df = pd.DataFrame(selected_rows)
    rows: List[Dict[str, Any]] = []
    if not selected_df.empty:
        metric_map = [
            ("hit_5pct_5d", "hit_5pct_5d_rate"),
            ("hit_10pct_10d", "hit_10pct_10d_rate"),
            ("fwd_max_ret_5d", "avg_fwd_max_ret_5d"),
            ("fwd_max_ret_10d", "avg_fwd_max_ret_10d"),
            ("close_hit_5pct_5d", "close_hit_5pct_5d_rate"),
            ("close_hit_10pct_10d", "close_hit_10pct_10d_rate"),
            ("fwd_close_ret_5d", "avg_fwd_close_ret_5d"),
            ("fwd_close_ret_10d", "avg_fwd_close_ret_10d"),
            ("fwd_min_ret_5d", "avg_fwd_min_ret_5d"),
            ("fwd_min_ret_10d", "avg_fwd_min_ret_10d"),
        ]
        for value_col, metric_name in metric_map:
            baseline_values = random_dist[metric_name] if not random_dist.empty and metric_name in random_dist.columns else None
            res = engine.validate_grouped_mean(
                selected_df,
                value_col=value_col,
                group_col="as_of",
                baseline_values=baseline_values,
                metric=metric_name,
                higher_is_better=True,
            )
            rows.append(res.to_dict())

    trade_df = pd.DataFrame(trade_rows)
    if trade_random_dist is None:
        trade_random_dist = pd.DataFrame()

    if not trade_df.empty and "trade_return" in trade_df.columns:
        trade_baseline = trade_random_dist["trade_return_mean"] if not trade_random_dist.empty and "trade_return_mean" in trade_random_dist.columns else None
        res = engine.validate_grouped_mean(
            trade_df,
            value_col="trade_return",
            group_col="as_of",
            baseline_values=trade_baseline,
            metric="trade_return_mean",
            higher_is_better=True,
        )
        rows.append(res.to_dict())

        portfolio_df = _portfolio_returns_by_date(trade_rows, top_n=top_n)
        if not portfolio_df.empty:
            port_baseline = trade_random_dist["portfolio_return_by_date_mean"] if not trade_random_dist.empty and "portfolio_return_by_date_mean" in trade_random_dist.columns else None
            res = engine.validate_grouped_mean(
                portfolio_df,
                value_col="portfolio_return",
                group_col="as_of",
                baseline_values=port_baseline,
                metric="portfolio_return_by_date_mean",
                higher_is_better=True,
            )
            rows.append(res.to_dict())

    return pd.DataFrame(rows), random_dist


def _daily_summary(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame()
    grouped = []
    for as_of, g in df.groupby("as_of"):
        grouped.append({"as_of": as_of, **_summarize_rows(g.to_dict("records"), int(g["rank"].max()))})
    return pd.DataFrame(grouped).sort_values("as_of")


def _monthly_summary(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame()
    df["month"] = pd.to_datetime(df["as_of"]).dt.to_period("M").astype(str)
    grouped = []
    for month, g in df.groupby("month"):
        grouped.append({"month": month, **_summarize_rows(g.to_dict("records"), int(g["rank"].max()))})
    return pd.DataFrame(grouped).sort_values("month")


def _bucket_summary(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame()
    bins = [0, 40, 60, 75, 100]
    labels = ["0-40", "40-60", "60-75", "75-100"]
    df["score_bucket"] = pd.cut(pd.to_numeric(df["suitability_score"], errors="coerce"), bins=bins, labels=labels, include_lowest=True)
    out = []
    for bucket, g in df.groupby("score_bucket", observed=True):
        out.append({"score_bucket": str(bucket), **_summarize_rows(g.to_dict("records"), int(g["rank"].max()))})
    return pd.DataFrame(out)



def _write_html_report(
    path: str,
    summary: Dict[str, Any],
    daily: pd.DataFrame,
    monthly: pd.DataFrame,
    bucket: pd.DataFrame,
    detail: pd.DataFrame,
    top_list: Optional[pd.DataFrame] = None,
    random_df: Optional[pd.DataFrame] = None,
    alpha_df: Optional[pd.DataFrame] = None,
    trade_summary_df: Optional[pd.DataFrame] = None,
    trade_df: Optional[pd.DataFrame] = None,
    grid_search_df: Optional[pd.DataFrame] = None,
    statistics_df: Optional[pd.DataFrame] = None,
    trade_random_distribution_df: Optional[pd.DataFrame] = None,
    portfolio_by_date_df: Optional[pd.DataFrame] = None,
) -> None:
    def table(df: pd.DataFrame, max_rows: int = 200) -> str:
        if df is None or df.empty:
            return "<p>No data</p>"
        return df.head(max_rows).to_html(index=False, float_format=lambda x: f"{x:.4f}", border=0, classes="table")

    html = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<title>Phoenix Quant Benchmark</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 32px; color: #222; }}
.card {{ border: 1px solid #ddd; border-radius: 12px; padding: 18px; margin: 16px 0; }}
.kpi {{ display: inline-block; min-width: 180px; margin: 8px 16px 8px 0; }}
.kpi b {{ display:block; font-size: 22px; }}
.warn {{ background: #fff7e6; border: 1px solid #ffd591; border-radius: 10px; padding: 12px; margin: 16px 0; }}
.danger {{ background: #fff1f0; border: 1px solid #ffa39e; border-radius: 10px; padding: 12px; margin: 16px 0; }}
.table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
.table th, .table td {{ border-bottom: 1px solid #eee; padding: 6px 8px; text-align: right; }}
.table th:first-child, .table td:first-child {{ text-align: left; }}
code {{ background: #f5f5f5; padding: 2px 5px; border-radius: 4px; }}
</style>
</head>
<body>
<h1>Phoenix Quant Benchmark</h1>
<div class="card">
  <div class="kpi">5D +5% Hit Rate<b>{_pct(summary.get('hit_5pct_5d_rate'))}</b></div>
  <div class="kpi">10D +10% Hit Rate<b>{_pct(summary.get('hit_10pct_10d_rate'))}</b></div>
  <div class="kpi">Avg 5D Max Return<b>{_pct(summary.get('avg_fwd_max_ret_5d'))}</b></div>
  <div class="kpi">Avg 10D Max Return<b>{_pct(summary.get('avg_fwd_max_ret_10d'))}</b></div>
  <div class="kpi">5D Close +5% Hit<b>{_pct(summary.get('close_hit_5pct_5d_rate'))}</b></div>
  <div class="kpi">Avg 5D Close Return<b>{_pct(summary.get('avg_fwd_close_ret_5d'))}</b></div>
  <div class="kpi">Avg 5D Worst Low<b>{_pct(summary.get('avg_fwd_min_ret_5d'))}</b></div>
  <div class="kpi">Sharpe 5D<b>{_fmt(summary.get('sharpe_5d'))}</b></div>
  <div class="kpi">MDD 5D<b>{_pct(summary.get('mdd_5d'))}</b></div>
  <div class="kpi">Trades<b>{summary.get('n_trades', 0)}</b></div>
  <div class="kpi">Dates<b>{summary.get('n_dates', 0)}</b></div>
</div>
<div class="warn">
  <b>Methodology Note</b><br/>
  Statistical Validation uses <code>block bootstrap by as_of date</code>. Trade random baseline uses cached candidate trade outcomes, not repeated engine simulation.
</div>
<div class="danger">
  <b>Grid Search Warning</b><br/>
  This grid search is <code>in-sample exploratory only</code>. Do not interpret the top-ranked rule as an optimized trading parameter. Use this section for parameter sensitivity inspection only. Out-of-sample validation is required before using any rule.
</div>
<h2>Statistical Validation</h2>{table(statistics_df)}
<h2>Top N / Alpha Summary</h2>{table(alpha_df)}
<h2>Top N Summary</h2>{table(top_list)}
<h2>Random Baseline</h2>{table(random_df)}
<h2>Trade Rule Grid Search - In-sample Exploratory</h2>{table(grid_search_df)}
<h2>Trade Simulation Summary</h2>{table(trade_summary_df)}
<h2>Portfolio Returns by Date</h2>{table(portfolio_by_date_df)}
<h2>Trade Random Distribution</h2>{table(trade_random_distribution_df)}
<h2>Trade Simulation Detail</h2>{table(trade_df, max_rows=500)}
<h2>Monthly Summary</h2>{table(monthly)}
<h2>Score Bucket Summary</h2>{table(bucket)}
<h2>Daily Summary</h2>{table(daily)}
<h2>Detail</h2>{table(detail, max_rows=500)}
</body>
</html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def _benchmark_output_paths(out_dir: str) -> Dict[str, str]:
    return {
        "summary": os.path.join(out_dir, "benchmark_summary.csv"),
        "detail": os.path.join(out_dir, "benchmark_detail.csv"),
        "ranked_detail": os.path.join(out_dir, "benchmark_ranked_detail.csv"),
        "candidates": os.path.join(out_dir, "benchmark_candidates.csv"),
        "top_list": os.path.join(out_dir, "benchmark_top_list.csv"),
        "random": os.path.join(out_dir, "benchmark_random_baseline.csv"),
        "alpha": os.path.join(out_dir, "benchmark_alpha.csv"),
        "trade": os.path.join(out_dir, "benchmark_trade_sim.csv"),
        "trade_summary": os.path.join(out_dir, "benchmark_trade_summary.csv"),
        "trade_random_distribution": os.path.join(out_dir, "benchmark_trade_random_distribution.csv"),
        "portfolio_by_date": os.path.join(out_dir, "benchmark_portfolio_by_date.csv"),
        "grid_search": os.path.join(out_dir, "benchmark_trade_grid_search.csv"),
        "statistics": os.path.join(out_dir, "benchmark_statistics.csv"),
        "random_distribution": os.path.join(out_dir, "benchmark_random_distribution.csv"),
        "daily": os.path.join(out_dir, "benchmark_daily.csv"),
        "monthly": os.path.join(out_dir, "benchmark_monthly.csv"),
        "rank_mode_comparison": os.path.join(out_dir, "benchmark_rank_mode_comparison.csv"),
        "bucket": os.path.join(out_dir, "benchmark_score_buckets.csv"),
        "failed": os.path.join(out_dir, "benchmark_failed_dates.csv"),
        "html": os.path.join(out_dir, "benchmark_report.html"),
        "partial_candidates": os.path.join(out_dir, "benchmark_candidates_partial.csv"),
        "partial_ranked_detail": os.path.join(out_dir, "benchmark_ranked_detail_partial.csv"),
        "partial_failed": os.path.join(out_dir, "benchmark_failed_dates_partial.csv"),
    }


def _read_partial_rows(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    try:
        return pd.read_csv(path).to_dict("records")
    except pd.errors.EmptyDataError:
        return []


def _write_benchmark_partials(paths: Dict[str, str], candidate_rows: List[Dict[str, Any]], detail_rows: List[Dict[str, Any]], failed_dates: List[Dict[str, str]]) -> None:
    pd.DataFrame(candidate_rows).to_csv(paths["partial_candidates"], index=False, encoding="utf-8-sig")
    pd.DataFrame(detail_rows).to_csv(paths["partial_ranked_detail"], index=False, encoding="utf-8-sig")
    pd.DataFrame(failed_dates).to_csv(paths["partial_failed"], index=False, encoding="utf-8-sig")


def _completed_partial_dates(candidate_rows: List[Dict[str, Any]], detail_rows: List[Dict[str, Any]], failed_dates: List[Dict[str, str]]) -> set[str]:
    completed: set[str] = set()
    for rows in (candidate_rows, detail_rows):
        for row in rows:
            if "as_of" in row and pd.notna(row.get("as_of")):
                completed.add(pd.Timestamp(row["as_of"]).date().isoformat())
    for row in failed_dates:
        if "as_of" in row and pd.notna(row.get("as_of")):
            completed.add(pd.Timestamp(row["as_of"]).date().isoformat())
    return completed


def run_benchmark(app_config: AppConfig, bench: BenchmarkConfig) -> Dict[str, Any]:
    bootstrap.init()
    _ensure_dirs(app_config)
    k = bench.k or app_config.similarity_k
    tickers = list(dict.fromkeys(app_config.universe + app_config.market_etfs))
    print("[1/5] 전체 데이터 다운로드/로드 중...")
    full_raw = download_ohlcv(tickers, cache_dir=app_config.cache_dir, period=bench.period, force_refresh=bench.refresh)
    if "SPY" not in full_raw:
        raise RuntimeError("SPY 데이터가 필요합니다.")
    dates = _select_asof_dates(full_raw["SPY"], bench.start, bench.end, bench.frequency, bench.max_dates)
    print(f"[2/5] 기준일 {len(dates)}개 선택됨 ({bench.frequency})")

    if bench.resume_dir:
        out_dir = bench.resume_dir
        os.makedirs(out_dir, exist_ok=True)
        print(f"  [resume] 기존 benchmark 디렉터리 사용: {out_dir}")
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = os.path.join(app_config.reports_dir, f"benchmark_{stamp}")
        os.makedirs(out_dir, exist_ok=True)
        print(f"  [checkpoint] partial 저장 위치: {out_dir}")
    paths = _benchmark_output_paths(out_dir)

    detail_rows: List[Dict[str, Any]] = _read_partial_rows(paths["partial_ranked_detail"])
    candidate_rows: List[Dict[str, Any]] = _read_partial_rows(paths["partial_candidates"])
    failed_dates: List[Dict[str, str]] = _read_partial_rows(paths["partial_failed"])
    completed_dates = _completed_partial_dates(candidate_rows, detail_rows, failed_dates)
    if completed_dates:
        print(f"  [resume] 완료된 기준일 {len(completed_dates)}개 로드")
    top_values = _parse_top_list(bench.top_list, bench.top_n)
    max_top_n = max(top_values)

    for i, as_of in enumerate(dates, start=1):
        as_of_str = as_of.date().isoformat()
        if as_of_str in completed_dates:
            print(f"[asof {i}/{len(dates)}] {as_of_str} skip (partial exists)")
            continue
        print(f"[asof {i}/{len(dates)}] {as_of_str}")
        try:
            train_raw = _slice_raw_until(full_raw, as_of)
            prebuilt = _build_prebuilt_for_asof(app_config, train_raw, retrain=True, k=k)
            ranking_engine = EngineRegistry.get("ranking_engine", app_config.engines.get("ranking_engine", "ranking_v1"))
            xgb_model = None
            try:
                xgb_model = ranking_engine._fit_xgb_model(prebuilt.get("records", []))
            except Exception as exc:  # noqa: BLE001
                logging.getLogger("benchmark").debug("%s xgb disabled: %s", as_of_str, exc)
            rows_for_date: List[Dict[str, Any]] = []
            for ticker in app_config.universe:
                if ticker not in train_raw:
                    continue
                try:
                    decision, meta = analyze_ticker_quiet(
                        app_config,
                        ticker,
                        period=bench.period,
                        refresh=False,
                        retrain=False,
                        k=k,
                        raw_data=train_raw,
                        prebuilt=prebuilt,
                    )
                    xgb_score = _score_decision_with_ranking_model(ranking_engine, xgb_model, prebuilt, decision)
                    row = _row_from_decision(
                        0,
                        decision,
                        meta,
                        full_raw,
                        xgb_score=xgb_score,
                        xgb_blend_weight=_primary_xgb_blend_weight(bench.xgb_blend_weights),
                    )
                    rows_for_date.append(row)
                except Exception as exc:  # noqa: BLE001
                    logging.getLogger("benchmark").debug("%s %s skip: %s", as_of_str, ticker, exc)
            ranked_rows = _rank_candidate_rows(
                rows_for_date,
                _primary_rank_mode(bench.rank_mode),
                _primary_xgb_blend_weight(bench.xgb_blend_weights),
            )
            # 전체 후보는 random baseline 계산용으로 저장한다.
            for row in ranked_rows:
                candidate_rows.append(row)
                if int(row.get("rank", 999999)) <= max_top_n:
                    detail_rows.append(row)
            completed_dates.add(as_of_str)
            _write_benchmark_partials(paths, candidate_rows, detail_rows, failed_dates)
            print(f"  [checkpoint] saved partial ({len(completed_dates)}/{len(dates)})")
        except Exception as exc:  # noqa: BLE001
            failed_dates.append({"as_of": as_of_str, "error": str(exc)})
            completed_dates.add(as_of_str)
            _write_benchmark_partials(paths, candidate_rows, detail_rows, failed_dates)
            print(f"  [warn] {as_of_str} 실패: {exc}")

    print("[3/5] 결과 집계 중...")
    if bench.drop_incomplete_future:
        required_days = _required_future_days(bench)
        before_detail = len(detail_rows)
        before_candidates = len(candidate_rows)
        detail_rows = _filter_complete_future_rows(detail_rows, full_raw, required_days)
        candidate_rows = _filter_complete_future_rows(candidate_rows, full_raw, required_days)
        print(f"  [info] incomplete future window 제거: detail {before_detail}->{len(detail_rows)}, candidates {before_candidates}->{len(candidate_rows)} (required={required_days} trading days)")

    print(f"  [info] v2.0 execution filters는 종목 제거가 아니라 cash slot으로 처리합니다. min_price={bench.min_price}, min_dollar_volume={bench.min_dollar_volume}, max_gap_open={bench.max_gap_open}, entry_penalty_bps={bench.entry_penalty_bps}")

    # detail_rows에는 max(top_list)까지 저장되어 있으므로 기본 summary는 bench.top_n 기준으로 다시 자른다.
    selected_rows = _rows_for_top_n(detail_rows, bench.top_n)
    trade_rows: List[Dict[str, Any]] = []
    trade_summary: Dict[str, Any] = {}
    grid_search_df = pd.DataFrame()
    trade_random_distribution_df = pd.DataFrame()
    portfolio_by_date_df = pd.DataFrame()
    rank_mode_comparison_df = _rank_mode_comparison(candidate_rows, full_raw, bench)

    if bench.trade_sim:
        trade_rows, trade_summary = _build_trade_rows_with_engine_v20(
            selected_rows,
            full_raw,
            take_profit=bench.take_profit,
            stop_loss=bench.stop_loss,
            hold_days=bench.hold_days,
            same_day_rule=bench.same_day_rule,
            entry_mode=bench.entry_mode,
            fee_bps=bench.fee_bps,
            slippage_bps=bench.slippage_bps,
            min_dollar_volume=bench.min_dollar_volume,
            min_price=bench.min_price,
            max_gap_open=bench.max_gap_open,
            entry_penalty_bps=bench.entry_penalty_bps,
            top_n=bench.top_n,
        )
        portfolio_by_date_df = _portfolio_returns_by_date(trade_rows, top_n=bench.top_n)

        if bench.random_baseline > 0:
            trade_cache = _build_trade_outcome_cache(
                candidate_rows,
                full_raw,
                take_profit=bench.take_profit,
                stop_loss=bench.stop_loss,
                hold_days=bench.hold_days,
                same_day_rule=bench.same_day_rule,
                entry_mode=bench.entry_mode,
                fee_bps=bench.fee_bps,
                slippage_bps=bench.slippage_bps,
                min_dollar_volume=bench.min_dollar_volume,
                min_price=bench.min_price,
                max_gap_open=bench.max_gap_open,
                entry_penalty_bps=bench.entry_penalty_bps,
                top_n=bench.top_n,
            )
            trade_random_distribution_df = _random_trade_distribution(
                candidate_rows,
                trade_cache,
                top_n=bench.top_n,
                iterations=bench.random_baseline,
                seed=bench.random_seed,
            )

    if bench.grid_search:
        tp_values = _parse_float_list(bench.tp_list, [0.03, 0.04, 0.05, 0.06, 0.08])
        sl_values = _parse_float_list(bench.sl_list, [0.02, 0.03, 0.04])
        hold_values = _parse_int_list(bench.hold_list, [3, 5, 7, 10])
        grid_search_df = _grid_search_trade_rules(
            selected_rows,
            full_raw,
            tp_values=tp_values,
            sl_values=sl_values,
            hold_values=hold_values,
            same_day_rule=bench.same_day_rule,
            entry_mode=bench.entry_mode,
            fee_bps=bench.fee_bps,
            slippage_bps=bench.slippage_bps,
            min_dollar_volume=bench.min_dollar_volume,
            min_price=bench.min_price,
            max_gap_open=bench.max_gap_open,
            entry_penalty_bps=bench.entry_penalty_bps,
            top_n=bench.top_n,
        )

    if trade_summary:
        # v1.5/v1.8 출력/CSV 호환 alias
        # v2.0: avg_trade_return은 cash slot 포함 slot 평균을 우선 사용한다.
        trade_summary["avg_trade_return"] = trade_summary.get("slot_avg_return", trade_summary.get("avg_return", np.nan))
        trade_summary["median_trade_return"] = trade_summary.get("slot_median_return", trade_summary.get("median_return", np.nan))
        trade_summary["cum_return_equal_weight"] = trade_summary.get("cumulative_return", np.nan)
        trade_summary["mdd_trade"] = trade_summary.get("portfolio_mdd", trade_summary.get("mdd", np.nan))
        trade_summary["profit_factor_trade"] = trade_summary.get("profit_factor", np.nan)
        trade_summary["take_profit_rate"] = trade_summary.get("tp_rate", np.nan)
        trade_summary["stop_loss_rate"] = trade_summary.get("sl_rate", np.nan)
        trade_summary["time_exit_rate"] = trade_summary.get("time_exit_rate", np.nan)
        if trade_random_distribution_df is not None and not trade_random_distribution_df.empty:
            trade_summary["random_trade_return_mean"] = float(trade_random_distribution_df["trade_return_mean"].mean())
            trade_summary["random_portfolio_return_by_date_mean"] = float(trade_random_distribution_df["portfolio_return_by_date_mean"].mean())
            trade_summary["alpha_trade_return_mean"] = float(trade_summary.get("avg_trade_return", np.nan)) - trade_summary["random_trade_return_mean"]
            if portfolio_by_date_df is not None and not portfolio_by_date_df.empty:
                trade_summary["portfolio_return_by_date_mean"] = float(portfolio_by_date_df["portfolio_return"].mean())
                trade_summary["alpha_portfolio_return_by_date_mean"] = trade_summary["portfolio_return_by_date_mean"] - trade_summary["random_portfolio_return_by_date_mean"]

    statistics_df, random_distribution_df = _run_statistical_validation(
        selected_rows,
        candidate_rows,
        trade_rows,
        top_n=bench.top_n,
        random_iterations=bench.random_baseline,
        random_seed=bench.random_seed,
        bootstrap_iterations=bench.bootstrap,
        confidence_level=bench.confidence_level,
        trade_random_dist=trade_random_distribution_df,
    )

    detail_df = pd.DataFrame(selected_rows)
    trade_df = pd.DataFrame(trade_rows)
    trade_summary_df = pd.DataFrame([trade_summary]) if trade_summary else pd.DataFrame()
    candidate_df = pd.DataFrame(candidate_rows)
    ranked_detail_df = pd.DataFrame(detail_rows)
    daily_df = _daily_summary(selected_rows)
    monthly_df = _monthly_summary(selected_rows)
    bucket_df = _bucket_summary(selected_rows)
    top_list_df = _top_list_summary(detail_rows, top_values)
    random_df = _random_baseline_summary(candidate_rows, top_values, bench.random_baseline, bench.random_seed)
    alpha_df = _merge_alpha_summary(top_list_df, random_df)
    summary = _summarize_rows(selected_rows, bench.top_n)
    summary["start"] = bench.start
    summary["end"] = bench.end
    summary["frequency"] = bench.frequency
    summary["min_price"] = bench.min_price
    summary["min_dollar_volume"] = bench.min_dollar_volume
    summary["max_gap_open"] = np.nan if bench.max_gap_open is None else bench.max_gap_open
    summary["entry_penalty_bps"] = bench.entry_penalty_bps
    summary["rank_mode"] = bench.rank_mode
    summary["primary_rank_mode"] = _primary_rank_mode(bench.rank_mode)
    summary["xgb_blend_weight"] = _primary_xgb_blend_weight(bench.xgb_blend_weights)
    summary["xgb_blend_weights"] = ",".join(str(w) for w in bench.xgb_blend_weights)
    summary["failed_dates"] = len(failed_dates)
    summary_df = pd.DataFrame([summary])

    print("[4/5] CSV/HTML 저장 중...")
    summary_df.to_csv(paths["summary"], index=False, encoding="utf-8-sig")
    detail_df.to_csv(paths["detail"], index=False, encoding="utf-8-sig")
    ranked_detail_df.to_csv(paths["ranked_detail"], index=False, encoding="utf-8-sig")
    candidate_df.to_csv(paths["candidates"], index=False, encoding="utf-8-sig")
    top_list_df.to_csv(paths["top_list"], index=False, encoding="utf-8-sig")
    random_df.to_csv(paths["random"], index=False, encoding="utf-8-sig")
    alpha_df.to_csv(paths["alpha"], index=False, encoding="utf-8-sig")
    trade_df.to_csv(paths["trade"], index=False, encoding="utf-8-sig")
    trade_summary_df.to_csv(paths["trade_summary"], index=False, encoding="utf-8-sig")
    trade_random_distribution_df.to_csv(paths["trade_random_distribution"], index=False, encoding="utf-8-sig")
    portfolio_by_date_df.to_csv(paths["portfolio_by_date"], index=False, encoding="utf-8-sig")
    grid_search_df.to_csv(paths["grid_search"], index=False, encoding="utf-8-sig")
    statistics_df.to_csv(paths["statistics"], index=False, encoding="utf-8-sig")
    random_distribution_df.to_csv(paths["random_distribution"], index=False, encoding="utf-8-sig")
    daily_df.to_csv(paths["daily"], index=False, encoding="utf-8-sig")
    monthly_df.to_csv(paths["monthly"], index=False, encoding="utf-8-sig")
    rank_mode_comparison_df.to_csv(paths["rank_mode_comparison"], index=False, encoding="utf-8-sig")
    bucket_df.to_csv(paths["bucket"], index=False, encoding="utf-8-sig")
    pd.DataFrame(failed_dates).to_csv(paths["failed"], index=False, encoding="utf-8-sig")
    _write_html_report(paths["html"], summary, daily_df, monthly_df, bucket_df, detail_df, top_list_df, random_df, alpha_df, trade_summary_df, trade_df, grid_search_df, statistics_df, trade_random_distribution_df, portfolio_by_date_df)

    print("[5/5] 완료")
    return {"summary": summary, "paths": paths, "failed_dates": failed_dates, "top_list": top_list_df, "random": random_df, "alpha": alpha_df, "trade_summary": trade_summary_df, "trade": trade_df, "grid_search": grid_search_df, "statistics": statistics_df, "random_distribution": random_distribution_df, "trade_random_distribution": trade_random_distribution_df, "portfolio_by_date": portfolio_by_date_df, "rank_mode_comparison": rank_mode_comparison_df, "selected_rows": selected_rows, "candidate_rows": candidate_rows, "full_raw": full_raw}




def _validate_trading_day_embargo(spy: pd.DataFrame, train_end: str, test_start: str, embargo_trading_days: int) -> Dict[str, Any]:
    idx = pd.DatetimeIndex(spy.index).sort_values()
    train_ts = pd.Timestamp(train_end)
    test_ts = pd.Timestamp(test_start)
    train_prior = idx[idx <= train_ts]
    if len(train_prior) == 0:
        raise ValueError(f"train_end 이전 거래일을 찾을 수 없습니다: {train_end}")
    train_last = pd.Timestamp(train_prior[-1])
    pos = idx.get_loc(train_last)
    if isinstance(pos, slice):
        pos = pos.start
    pos = int(pos)
    required_pos = min(pos + int(embargo_trading_days) + 1, len(idx) - 1)
    min_test_start = pd.Timestamp(idx[required_pos])
    if test_ts < min_test_start:
        raise ValueError(
            f"Embargo violation: test_start={test_start} < min_allowed={min_test_start.date()} "
            f"(train_end={train_last.date()}, embargo_trading_days={embargo_trading_days})"
        )
    return {
        "train_last_trading_day": train_last.date().isoformat(),
        "min_test_start_after_embargo": min_test_start.date().isoformat(),
        "test_start": test_ts.date().isoformat(),
        "embargo_trading_days": int(embargo_trading_days),
    }


def _rule_key(row: Dict[str, Any]) -> str:
    return f"TP{float(row['take_profit']):.4f}_SL{float(row['stop_loss']):.4f}_H{int(row['hold_days'])}"


def _extract_metric_from_stats(stats_df: pd.DataFrame, metric: str, field: str, default=np.nan):
    if stats_df is None or stats_df.empty:
        return default
    sub = stats_df[stats_df["metric"] == metric]
    if sub.empty or field not in sub.columns:
        return default
    return sub.iloc[0].get(field, default)


def _evaluate_fixed_rule_on_rows(
    *,
    selected_rows: List[Dict[str, Any]],
    candidate_rows: List[Dict[str, Any]],
    full_raw: Dict[str, pd.DataFrame],
    bench: BenchmarkConfig,
    take_profit: float,
    stop_loss: float,
    hold_days: int,
    rule_name: str,
    rule_source: str,
) -> Dict[str, Any]:
    trade_rows, trade_summary = _build_trade_rows_with_engine_v20(
        selected_rows,
        full_raw,
        take_profit=take_profit,
        stop_loss=stop_loss,
        hold_days=hold_days,
        same_day_rule=bench.same_day_rule,
        entry_mode=bench.entry_mode,
        fee_bps=bench.fee_bps,
        slippage_bps=bench.slippage_bps,
        min_dollar_volume=bench.min_dollar_volume,
        min_price=bench.min_price,
        max_gap_open=bench.max_gap_open,
        entry_penalty_bps=bench.entry_penalty_bps,
        top_n=bench.top_n,
    )
    trade_cache = _build_trade_outcome_cache(
        candidate_rows,
        full_raw,
        take_profit=take_profit,
        stop_loss=stop_loss,
        hold_days=hold_days,
        same_day_rule=bench.same_day_rule,
        entry_mode=bench.entry_mode,
        fee_bps=bench.fee_bps,
        slippage_bps=bench.slippage_bps,
        min_dollar_volume=bench.min_dollar_volume,
        min_price=bench.min_price,
        max_gap_open=bench.max_gap_open,
        entry_penalty_bps=bench.entry_penalty_bps,
        top_n=bench.top_n,
    )
    trade_random_dist = _random_trade_distribution(
        candidate_rows,
        trade_cache,
        top_n=bench.top_n,
        iterations=bench.random_baseline,
        seed=bench.random_seed,
    )
    stats_df, _random_dist = _run_statistical_validation(
        selected_rows,
        candidate_rows,
        trade_rows,
        top_n=bench.top_n,
        random_iterations=bench.random_baseline,
        random_seed=bench.random_seed,
        bootstrap_iterations=bench.bootstrap,
        confidence_level=bench.confidence_level,
        trade_random_dist=trade_random_dist,
    )
    port = _portfolio_returns_by_date(trade_rows, top_n=bench.top_n)
    out = {
        "rule_name": rule_name,
        "rule_source": rule_source,
        "take_profit": float(take_profit),
        "stop_loss": float(stop_loss),
        "hold_days": int(hold_days),
        "n_slots": int(trade_summary.get("n_slots", len(trade_rows))),
        "n_active_trades": int(trade_summary.get("n_active_trades", 0)),
        "cash_slots": int(trade_summary.get("cash_slots", 0)),
        "cash_weight_mean": float(trade_summary.get("cash_weight_mean", np.nan)),
        "active_avg_return": float(trade_summary.get("active_avg_return", np.nan)),
        "slot_avg_return": float(trade_summary.get("slot_avg_return", np.nan)),
        "portfolio_return_by_date_mean": float(trade_summary.get("portfolio_return_by_date_mean", np.nan)),
        "portfolio_return_by_date_median": float(trade_summary.get("portfolio_return_by_date_median", np.nan)),
        "portfolio_positive_date_rate": float(trade_summary.get("portfolio_positive_date_rate", np.nan)),
        "portfolio_mdd": float(trade_summary.get("portfolio_mdd", np.nan)),
        "portfolio_random_mean": _extract_metric_from_stats(stats_df, "portfolio_return_by_date_mean", "baseline_mean"),
        "portfolio_alpha": _extract_metric_from_stats(stats_df, "portfolio_return_by_date_mean", "alpha"),
        "portfolio_p_value": _extract_metric_from_stats(stats_df, "portfolio_return_by_date_mean", "p_value"),
        "portfolio_random_z_score": _extract_metric_from_stats(stats_df, "portfolio_return_by_date_mean", "random_z_score"),
        "portfolio_ci_low": _extract_metric_from_stats(stats_df, "portfolio_return_by_date_mean", "ci_low"),
        "portfolio_ci_high": _extract_metric_from_stats(stats_df, "portfolio_return_by_date_mean", "ci_high"),
        "n_test_dates": int(port["as_of"].nunique()) if not port.empty else 0,
    }
    return out


def run_train_test_validation(app_config: AppConfig, bench: BenchmarkConfig) -> Dict[str, Any]:
    if not all([bench.train_start, bench.train_end, bench.test_start, bench.test_end]):
        raise ValueError("--train-test 사용 시 --train-start, --train-end, --test-start, --test-end가 필요합니다.")

    print("Phoenix Quant v2.0.1 Purged Train/Test Validation")
    print("[0/3] Embargo 검증 중...")
    _ensure_dirs(app_config)
    spy_raw = download_ohlcv(["SPY"], cache_dir=app_config.cache_dir, period=bench.period, force_refresh=bench.refresh)
    embargo_info = _validate_trading_day_embargo(spy_raw["SPY"], bench.train_end, bench.test_start, bench.embargo_trading_days)
    print(f"  [ok] train_last={embargo_info['train_last_trading_day']} / min_test_start={embargo_info['min_test_start_after_embargo']} / test_start={embargo_info['test_start']}")

    print("[1/3] Train benchmark + grid search")
    train_bench = replace(
        bench,
        start=bench.train_start,
        end=bench.train_end,
        trade_sim=True,
        grid_search=True,
        refresh=bench.refresh,
    )
    train_result = run_benchmark(app_config, train_bench)
    grid = train_result.get("grid_search", pd.DataFrame())
    if grid is None or grid.empty:
        raise RuntimeError("Train grid_search 결과가 없습니다.")

    # Train 상위 K + default rule. 중복 제거.
    rule_rows: List[Dict[str, Any]] = []
    for _, row in grid.head(int(bench.train_top_k_rules)).iterrows():
        d = row.to_dict()
        d["rule_source"] = "train_grid_top"
        d["rule_name"] = f"train_grid_rank_{int(row.get('grid_rank', len(rule_rows)+1))}"
        rule_rows.append(d)
    default_rule = {
        "take_profit": bench.take_profit,
        "stop_loss": bench.stop_loss,
        "hold_days": bench.hold_days,
        "rule_source": "default_rule",
        "rule_name": "default_rule",
    }
    rule_rows.append(default_rule)
    seen = set()
    unique_rules = []
    for r in rule_rows:
        key = (round(float(r["take_profit"]), 6), round(float(r["stop_loss"]), 6), int(r["hold_days"]))
        if key in seen:
            continue
        seen.add(key)
        unique_rules.append(r)

    print("[2/3] Test benchmark base run")
    first_rule = unique_rules[0]
    test_bench = replace(
        bench,
        start=bench.test_start,
        end=bench.test_end,
        take_profit=float(first_rule["take_profit"]),
        stop_loss=float(first_rule["stop_loss"]),
        hold_days=int(first_rule["hold_days"]),
        trade_sim=True,
        grid_search=False,
        refresh=False,
    )
    test_result = run_benchmark(app_config, test_bench)

    print("[3/3] Train-selected rules fixed evaluation on Test")
    oos_rows: List[Dict[str, Any]] = []
    for r in unique_rules:
        row = _evaluate_fixed_rule_on_rows(
            selected_rows=test_result["selected_rows"],
            candidate_rows=test_result["candidate_rows"],
            full_raw=test_result["full_raw"],
            bench=test_bench,
            take_profit=float(r["take_profit"]),
            stop_loss=float(r["stop_loss"]),
            hold_days=int(r["hold_days"]),
            rule_name=str(r.get("rule_name", _rule_key(r))),
            rule_source=str(r.get("rule_source", "train_grid_top")),
        )
        # train metrics attach
        for key in ["grid_rank", "profit_factor", "avg_return", "mdd", "portfolio_return_by_date_mean", "train_min_subperiod_return", "train_positive_subperiods", "stability_score"]:
            if key in r:
                row[f"train_{key}"] = r[key]
        oos_rows.append(row)

    oos_df = pd.DataFrame(oos_rows)
    if not oos_df.empty:
        oos_df = oos_df.sort_values(["portfolio_p_value", "portfolio_alpha", "portfolio_mdd"], ascending=[True, False, True]).reset_index(drop=True)
        oos_df.insert(0, "oos_rank", range(1, len(oos_df) + 1))

    if bench.resume_dir:
        out_dir = bench.resume_dir
        print(f"  [resume] final output dir: {out_dir}")
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = os.path.join(app_config.reports_dir, f"benchmark_train_test_{stamp}")
    os.makedirs(out_dir, exist_ok=True)
    paths = {
        "train_test_summary": os.path.join(out_dir, "benchmark_train_test_summary.csv"),
        "oos_rules": os.path.join(out_dir, "benchmark_oos_rules.csv"),
        "train_grid_search": os.path.join(out_dir, "benchmark_train_grid_search.csv"),
    }
    summary = {
        **embargo_info,
        "train_start": bench.train_start,
        "train_end": bench.train_end,
        "test_start": bench.test_start,
        "test_end": bench.test_end,
        "top_n": bench.top_n,
        "frequency": bench.frequency,
        "min_price": bench.min_price,
        "min_dollar_volume": bench.min_dollar_volume,
        "max_gap_open": np.nan if bench.max_gap_open is None else bench.max_gap_open,
        "entry_penalty_bps": bench.entry_penalty_bps,
        "rank_mode": bench.rank_mode,
        "xgb_blend_weights": ",".join(str(w) for w in bench.xgb_blend_weights),
        "train_report_dir": os.path.dirname(train_result["paths"]["summary"]),
        "test_report_dir": os.path.dirname(test_result["paths"]["summary"]),
    }
    pd.DataFrame([summary]).to_csv(paths["train_test_summary"], index=False, encoding="utf-8-sig")
    oos_df.to_csv(paths["oos_rules"], index=False, encoding="utf-8-sig")
    grid.to_csv(paths["train_grid_search"], index=False, encoding="utf-8-sig")

    print()
    print("Phoenix Quant v2.0.1 OOS Fixed Rule Results")
    print("━━━━━━━━━━━━━━━━━━━━")
    if not oos_df.empty:
        for _, row in oos_df.iterrows():
            print(
                f"- #{int(row['oos_rank'])} {row['rule_name']} "
                f"TP {_pct(row['take_profit'])} / SL {_pct(row['stop_loss'])} / Hold {int(row['hold_days'])}D "
                f"| OOS Portfolio {_pct(row['portfolio_return_by_date_mean'])} "
                f"/ Random {_pct(row['portfolio_random_mean'])} / Alpha {_pct(row['portfolio_alpha'])} "
                f"/ p={_fmt(row['portfolio_p_value'], 4)} / MDD {_pct(row['portfolio_mdd'])} "
                f"/ cash {_pct(row['cash_weight_mean'])}"
            )
    print("저장 위치:")
    for name, path in paths.items():
        print(f"- {name}: {path}")

    return {"summary": summary, "paths": paths, "oos_rules": oos_df, "train_result": train_result, "test_result": test_result}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Phoenix Quant Benchmark v2.0.1")
    p.add_argument("--config", default="config/config.yaml", help="config.yaml 경로")
    p.add_argument("--start", default=None, help="시작일 YYYY-MM-DD")
    p.add_argument("--end", default=None, help="종료일 YYYY-MM-DD")
    p.add_argument("--top-n", type=int, default=10, help="각 기준일마다 상위 N개를 채점")
    p.add_argument("--period", default="5y", help="yfinance 다운로드 기간. 백테스트는 넉넉히 5y 권장")
    p.add_argument("--frequency", choices=["daily", "weekly", "monthly"], default="monthly", help="기준일 샘플링 주기")
    p.add_argument("--max-dates", type=int, default=None, help="기준일 최대 개수. 너무 느릴 때 사용")
    p.add_argument("--refresh", action="store_true", help="OHLCV 캐시 무시하고 재다운로드")
    p.add_argument("--retrain", action="store_true", help="호환용 옵션. 벤치마크는 기준일마다 항상 재학습")
    p.add_argument("--k", type=int, default=None, help="유사도 검색 이웃 수")
    p.add_argument("--min-train-records", type=int, default=100, help="최소 학습 레코드 수")
    p.add_argument("--random-baseline", type=int, default=0, help="랜덤 TopN baseline 반복 횟수. 예: 1000")
    p.add_argument("--top-list", default=None, help="여러 TopN을 한 번에 비교. 예: 5,10,20,50")
    p.add_argument("--random-seed", type=int, default=42, help="랜덤 baseline 시드")
    p.add_argument("--rank-mode", choices=["decision", "ranking", "both"], default="decision", help="OOS TopN 정렬 기준: decision=suitability, ranking=final_rank_score, both=둘 다 비교")
    p.add_argument("--xgb-blend-weight", default="0.30", help="ranking 모드 XGB blend weight. 단일값 또는 comma grid 예: 0.0,0.1,0.2,0.3,0.4,0.5")
    p.add_argument("--bootstrap", type=int, default=1000, help="Block bootstrap 반복 횟수. 예: 1000")
    p.add_argument("--confidence-level", type=float, default=0.95, help="신뢰수준. 기본 0.95")
    p.add_argument("--trade-sim", action="store_true", help="실제 매매 시뮬레이션 실행")
    p.add_argument("--take-profit", type=float, default=0.05, help="익절 비율. 예: 0.05 = +5%%")
    p.add_argument("--stop-loss", type=float, default=0.03, help="손절 비율. 예: 0.03 = -3%%")
    p.add_argument("--hold-days", type=int, default=5, help="최대 보유 거래일 수")
    p.add_argument("--same-day-rule", choices=["stop_first", "take_first", "midpoint"], default="stop_first", help="일봉에서 익절/손절이 같은 날 모두 닿은 경우 처리 방식")
    p.add_argument("--entry-mode", choices=["close", "next_open"], default="next_open", help="진입가 기준. 실전형 기본값은 next_open")
    p.add_argument("--fee-bps", type=float, default=1.5, help="편도 수수료 bps. 기본 1.5bps")
    p.add_argument("--slippage-bps", type=float, default=5.0, help="편도 슬리피지 bps. 기본 5bps")
    p.add_argument("--grid-search", action="store_true", help="TP/SL/Hold 조합을 자동 탐색. 결과는 in-sample exploratory로만 해석")
    p.add_argument("--tp-list", default=None, help="Grid Search TP 목록. 예: 0.03,0.04,0.05,0.06")
    p.add_argument("--sl-list", default=None, help="Grid Search SL 목록. 예: 0.02,0.03,0.04")
    p.add_argument("--hold-list", default=None, help="Grid Search 보유일 목록. 예: 3,5,7,10")
    p.add_argument("--drop-incomplete-future", action=argparse.BooleanOptionalAction, default=True, help="미래 윈도우가 부족한 기준일 제거. 기본 True")
    p.add_argument("--min-dollar-volume", type=float, default=0.0, help="실행 필터용 as_of 기준 최소 거래대금. 실패 시 cash slot")
    p.add_argument("--min-price", type=float, default=0.0, help="실행 필터용 as_of 기준 최소 주가. 실패 시 cash slot")
    p.add_argument("--max-gap-open", type=float, default=None, help="next_open gap-up 최대 허용값. 예: 0.08 = +8%% 초과 시 cash slot")
    p.add_argument("--entry-penalty-bps", type=float, default=0.0, help="next_open 진입 보수성 페널티 bps. 수익률에서 추가 차감")
    p.add_argument("--resume-dir", default=None, help="partial CSV가 있는 benchmark 디렉터리에서 이어서 실행")
    p.add_argument("--train-test", action="store_true", help="v2.0 Purged Train/Test Validation 실행")
    p.add_argument("--train-start", default=None, help="Train 시작일 YYYY-MM-DD")
    p.add_argument("--train-end", default=None, help="Train 종료일 YYYY-MM-DD")
    p.add_argument("--test-start", default=None, help="Test 시작일 YYYY-MM-DD")
    p.add_argument("--test-end", default=None, help="Test 종료일 YYYY-MM-DD")
    p.add_argument("--embargo-trading-days", type=int, default=10, help="Train end 이후 제외할 거래일 수")
    p.add_argument("--train-top-k-rules", type=int, default=5, help="Train grid 상위 K개 룰을 Test에 고정 평가")
    p.set_defaults(
        top_n=5,
        top_list="5,10",
        min_dollar_volume=5_000_000.0,
        max_gap_open=0.08,
    )
    return p


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    args = build_parser().parse_args()
    app_config = load_config(args.config)
    bench = BenchmarkConfig(
        start=args.start or args.train_start or "2025-01-01",
        end=args.end or args.test_end or "2026-07-06",
        top_n=args.top_n,
        period=args.period,
        frequency=args.frequency,
        max_dates=args.max_dates,
        refresh=args.refresh,
        retrain=args.retrain,
        k=args.k,
        min_train_records=args.min_train_records,
        random_baseline=args.random_baseline,
        top_list=args.top_list,
        random_seed=args.random_seed,
        trade_sim=args.trade_sim,
        take_profit=args.take_profit,
        stop_loss=args.stop_loss,
        hold_days=args.hold_days,
        same_day_rule=args.same_day_rule,
        entry_mode=args.entry_mode,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        grid_search=args.grid_search,
        tp_list=args.tp_list,
        sl_list=args.sl_list,
        hold_list=args.hold_list,
        bootstrap=args.bootstrap,
        confidence_level=args.confidence_level,
        drop_incomplete_future=args.drop_incomplete_future,
        min_dollar_volume=args.min_dollar_volume,
        min_price=args.min_price,
        max_gap_open=args.max_gap_open,
        entry_penalty_bps=args.entry_penalty_bps,
        resume_dir=args.resume_dir,
        train_test=args.train_test,
        train_start=args.train_start,
        train_end=args.train_end,
        test_start=args.test_start,
        test_end=args.test_end,
        embargo_trading_days=args.embargo_trading_days,
        train_top_k_rules=args.train_top_k_rules,
        rank_mode=args.rank_mode,
        xgb_blend_weights=_parse_xgb_blend_weights(args.xgb_blend_weight),
    )
    if bench.train_test:
        run_train_test_validation(app_config, bench)
        return

    if not bench.start or not bench.end:
        raise ValueError("일반 benchmark 모드에서는 --start와 --end가 필요합니다.")

    result = run_benchmark(app_config, bench)
    s = result["summary"]
    print()
    print("Phoenix Quant Benchmark v2.0.1")
    print("━━━━━━━━━━━━━━━━━━━━")
    print(f"기간: {s['start']} ~ {s['end']} / frequency={s['frequency']} / Top{s['top_n']}")
    print(f"Rank Mode: {s.get('rank_mode')} / primary={s.get('primary_rank_mode')} / xgb_weight={s.get('xgb_blend_weight')}")
    print(f"기준일 수: {s['n_dates']} / 거래 수: {s['n_trades']} / 실패 기준일: {s['failed_dates']}")
    print(f"5D +5% Hit Rate: {_pct(s['hit_5pct_5d_rate'])}")
    print(f"10D +10% Hit Rate: {_pct(s['hit_10pct_10d_rate'])}")
    print(f"평균 5D 최대상승률: {_pct(s['avg_fwd_max_ret_5d'])}")
    print(f"평균 10D 최대상승률: {_pct(s['avg_fwd_max_ret_10d'])}")
    print(f"5D 종가 +5% Hit Rate: {_pct(s.get('close_hit_5pct_5d_rate'))}")
    print(f"10D 종가 +10% Hit Rate: {_pct(s.get('close_hit_10pct_10d_rate'))}")
    print(f"평균 5D 종가수익률: {_pct(s.get('avg_fwd_close_ret_5d'))} / 평균 5D 최저낙폭: {_pct(s.get('avg_fwd_min_ret_5d'))}")
    print(f"평균 10D 종가수익률: {_pct(s.get('avg_fwd_close_ret_10d'))} / 평균 10D 최저낙폭: {_pct(s.get('avg_fwd_min_ret_10d'))}")
    print(f"Sharpe 5D: {_fmt(s['sharpe_5d'])} / MDD 5D: {_pct(s['mdd_5d'])} / Profit Factor 5D: {_fmt(s['profit_factor_5d'])}")

    alpha_df = result.get("alpha")
    if alpha_df is not None and not alpha_df.empty:
        print()
        print("Top N / Random Alpha:")
        for _, row in alpha_df.iterrows():
            topn = int(row.get("top_n_eval", s["top_n"]))
            msg = f"- Top{topn}: Phoenix 5D {_pct(row.get('hit_5pct_5d_rate'))}"
            if pd.notna(row.get("random_hit_5pct_5d_mean", np.nan)):
                msg += f" / Random {_pct(row.get('random_hit_5pct_5d_mean'))} / Alpha {_pct(row.get('alpha_hit_5pct_5d'))}"
            msg += f" / Avg5D {_pct(row.get('avg_fwd_max_ret_5d'))}"
            if pd.notna(row.get("close_hit_5pct_5d_rate", np.nan)):
                msg += f" / CloseHit5D {_pct(row.get('close_hit_5pct_5d_rate'))}"
            if pd.notna(row.get("avg_fwd_min_ret_5d", np.nan)):
                msg += f" / WorstLow5D {_pct(row.get('avg_fwd_min_ret_5d'))}"
            print(msg)

    trade_summary_df = result.get("trade_summary")
    if trade_summary_df is not None and not trade_summary_df.empty:
        tr = trade_summary_df.iloc[0]
        print()
        print("Trade Simulation:")
        print(f"- Rule: Entry {tr.get('entry_mode', 'next_open')} / TP {_pct(tr.get('take_profit'))} / SL {_pct(tr.get('stop_loss'))} / Hold {int(tr.get('hold_days', 0))}D / same-day={tr.get('same_day_rule')} / fee {tr.get('fee_bps', 0)}bps / slip {tr.get('slippage_bps', 0)}bps / entry_penalty {tr.get('entry_penalty_bps', 0)}bps")
        print(f"- Active Win Rate: {_pct(tr.get('win_rate'))} / Slot Avg Return: {_pct(tr.get('avg_trade_return'))} / Slot Median: {_pct(tr.get('median_trade_return'))}")
        print(f"- Active positions: {int(tr.get('n_active_trades', tr.get('n_trades', 0)))} / Cash slots: {int(tr.get('cash_slots', 0))} / Avg cash weight: {_pct(tr.get('cash_weight_mean', 0))}")
        print(f"- Cum Return(active sequence): {_pct(tr.get('cum_return_equal_weight'))} / Portfolio MDD: {_pct(tr.get('mdd_trade'))} / Active PF: {_fmt(tr.get('profit_factor_trade'))}")
        print(f"- TP Rate: {_pct(tr.get('take_profit_rate'))} / SL Rate: {_pct(tr.get('stop_loss_rate'))} / Time Exit: {_pct(tr.get('time_exit_rate'))}")
        if pd.notna(tr.get("random_trade_return_mean", np.nan)):
            print(f"- Random Trade Avg: {_pct(tr.get('random_trade_return_mean'))} / Alpha: {_pct(tr.get('alpha_trade_return_mean'))}")
        if pd.notna(tr.get("portfolio_return_by_date_mean", np.nan)):
            print(f"- Portfolio by Date Avg: {_pct(tr.get('portfolio_return_by_date_mean'))} / Random: {_pct(tr.get('random_portfolio_return_by_date_mean'))} / Alpha: {_pct(tr.get('alpha_portfolio_return_by_date_mean'))}")

    comparison_df = result.get("rank_mode_comparison")
    if comparison_df is not None and not comparison_df.empty:
        print()
        print("Decision-only vs XGB-assisted Ranking:")
        for _, row in comparison_df.iterrows():
            print(
                f"- {row.get('rank_mode')} w={float(row.get('xgb_blend_weight', 0.0)):.2f} "
                f"| Portfolio {_pct(row.get('portfolio_return_by_date_mean'))} "
                f"/ Random {_pct(row.get('random_mean'))} / Alpha {_pct(row.get('alpha'))} "
                f"/ p={_fmt(row.get('p_value'), 4)} / MDD {_pct(row.get('mdd'))} "
                f"/ active {int(row.get('active_trades', 0))} / cash {int(row.get('cash_slots', 0))}"
            )

    statistics_df = result.get("statistics")
    if statistics_df is not None and not statistics_df.empty:
        print()
        print("Statistical Validation (Block Bootstrap by as_of):")
        for _, row in statistics_df.iterrows():
            metric = row.get("metric")
            msg = (
                f"- {metric}: obs {_pct(row.get('observed'))} / "
                f"CI [{_pct(row.get('ci_low'))}, {_pct(row.get('ci_high'))}] / "
                f"n={int(row.get('n', 0))}, dates={int(row.get('n_groups', 0))}"
            )
            if pd.notna(row.get("alpha", np.nan)):
                msg += f" / alpha {_pct(row.get('alpha'))}"
            if pd.notna(row.get("p_value", np.nan)):
                msg += f" / p={_fmt(row.get('p_value'), 4)}"
            if pd.notna(row.get("random_z_score", np.nan)):
                msg += f" / z={_fmt(row.get('random_z_score'), 2)}"
            print(msg)

    grid_search_df = result.get("grid_search")
    if grid_search_df is not None and not grid_search_df.empty:
        print()
        print("Trade Rule Grid Search Top 5 (in-sample exploratory):")
        for _, row in grid_search_df.head(5).iterrows():
            print(
                f"- #{int(row.get('grid_rank', 0))} "
                f"TP {_pct(row.get('take_profit'))} / SL {_pct(row.get('stop_loss'))} / Hold {int(row.get('hold_days', 0))}D "
                f"| Win {_pct(row.get('win_rate'))} / Avg {_pct(row.get('avg_return'))} "
                f"/ MDD {_pct(row.get('mdd'))} / PF {_fmt(row.get('profit_factor'))}"
            )

    print()
    print("저장 위치:")
    for name, path in result["paths"].items():
        print(f"- {name}: {path}")


if __name__ == "__main__":
    main()
