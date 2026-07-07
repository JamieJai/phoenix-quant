from __future__ import annotations

import argparse
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

from phoenix_core import bootstrap
from phoenix_core.config import AppConfig, load_config
from phoenix_core.data_loader import download_ohlcv
from phoenix_core.default_features import BASELINE_FEATURE_NAMES
from phoenix_core.models import RankingItem
from phoenix_core.pipeline import analyze_ticker_quiet, build_pattern_records
from phoenix_core.registry import EngineRegistry
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
            continue
        max_high = float(fut["High"].max())
        result[f"fwd_max_ret_{h}d"] = (max_high - close) / close
    result["hit_5pct_5d"] = 1.0 if result.get("fwd_max_ret_5d", np.nan) >= 0.05 else (np.nan if pd.isna(result.get("fwd_max_ret_5d", np.nan)) else 0.0)
    result["hit_10pct_10d"] = 1.0 if result.get("fwd_max_ret_10d", np.nan) >= 0.10 else (np.nan if pd.isna(result.get("fwd_max_ret_10d", np.nan)) else 0.0)
    return result


def _row_from_decision(rank: int, decision, meta: Dict[str, Any], full_raw: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    future = _future_result(full_raw, decision.ticker, decision.as_of)
    sector_rotation = meta.get("sector_rotation")
    target_strength = getattr(sector_rotation, "target_strength", None) if sector_rotation else None
    regime_result = meta.get("regime_result")
    return {
        "rank": rank,
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
        "hit_5pct_5d": future.get("hit_5pct_5d", np.nan),
        "hit_10pct_10d": future.get("hit_10pct_10d", np.nan),
    }


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
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    config = TradeConfig(
        take_profit=float(take_profit),
        stop_loss=float(stop_loss),
        max_hold_days=int(hold_days),
        trailing_stop=None,
        entry_mode=EntryMode.CLOSE,
        same_day_rule=SameDayRule(same_day_rule),
        fee_bps=0.0,
        slippage_bps=0.0,
    )
    engine = TradeSimulationEngine(config)
    candidates = _build_trade_candidates(selected_rows)
    results = engine.simulate_candidates(
        candidates=candidates,
        raw_data=full_raw,
        config=config,
    )
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

    result_by_key = {
        (r.ticker, str(r.as_of), int(r.rank)): r.to_dict()
        for r in results
    }

    rows: List[Dict[str, Any]] = []
    for row in selected_rows:
        key = (
            str(row.get("ticker", "")).upper(),
            str(pd.Timestamp(row.get("as_of")).date()),
            int(row.get("rank", 0) or 0),
        )
        merged = dict(row)
        trade = result_by_key.get(key, {})
        # v1.5 컬럼명과 호환되도록 alias를 같이 넣는다.
        if trade:
            merged.update(trade)
            merged["trade_return"] = trade.get("net_return", np.nan)
            merged["exit_reason"] = trade.get("exit_reason", "")
            merged["holding_days"] = trade.get("hold_days", np.nan)
        rows.append(merged)

    return rows, summary


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


def _write_html_report(path: str, summary: Dict[str, Any], daily: pd.DataFrame, monthly: pd.DataFrame, bucket: pd.DataFrame, detail: pd.DataFrame, top_list: Optional[pd.DataFrame] = None, random_df: Optional[pd.DataFrame] = None, alpha_df: Optional[pd.DataFrame] = None, trade_summary_df: Optional[pd.DataFrame] = None, trade_df: Optional[pd.DataFrame] = None) -> None:
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
  <div class="kpi">Sharpe 5D<b>{_fmt(summary.get('sharpe_5d'))}</b></div>
  <div class="kpi">MDD 5D<b>{_pct(summary.get('mdd_5d'))}</b></div>
  <div class="kpi">Trades<b>{summary.get('n_trades', 0)}</b></div>
  <div class="kpi">Dates<b>{summary.get('n_dates', 0)}</b></div>
</div>
<h2>Top N / Alpha Summary</h2>{table(alpha_df)}
<h2>Top N Summary</h2>{table(top_list)}
<h2>Random Baseline</h2>{table(random_df)}
<h2>Trade Simulation Summary</h2>{table(trade_summary_df)}
<h2>Trade Simulation Detail</h2>{table(trade_df, max_rows=500)}
<h2>Monthly Summary</h2>{table(monthly)}
<h2>Score Bucket Summary</h2>{table(bucket)}
<h2>Daily Summary</h2>{table(daily)}
<h2>Detail</h2>{table(detail, max_rows=500)}
</body>
</html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


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

    detail_rows: List[Dict[str, Any]] = []
    candidate_rows: List[Dict[str, Any]] = []
    failed_dates: List[Dict[str, str]] = []
    top_values = _parse_top_list(bench.top_list, bench.top_n)
    max_top_n = max(top_values)

    for i, as_of in enumerate(dates, start=1):
        as_of_str = as_of.date().isoformat()
        print(f"[asof {i}/{len(dates)}] {as_of_str}")
        try:
            train_raw = _slice_raw_until(full_raw, as_of)
            prebuilt = _build_prebuilt_for_asof(app_config, train_raw, retrain=True, k=k)
            decisions = []
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
                    decisions.append((decision, meta))
                except Exception as exc:  # noqa: BLE001
                    logging.getLogger("benchmark").debug("%s %s skip: %s", as_of_str, ticker, exc)
            decisions.sort(key=lambda dm: (dm[0].suitability_score, dm[0].confidence_score), reverse=True)
            # 전체 후보는 random baseline 계산용으로 저장한다.
            for rank, (decision, meta) in enumerate(decisions, start=1):
                row = _row_from_decision(rank, decision, meta, full_raw)
                candidate_rows.append(row)
                if rank <= max_top_n:
                    detail_rows.append(row)
        except Exception as exc:  # noqa: BLE001
            failed_dates.append({"as_of": as_of_str, "error": str(exc)})
            print(f"  [warn] {as_of_str} 실패: {exc}")

    print("[3/5] 결과 집계 중...")
    # detail_rows에는 max(top_list)까지 저장되어 있으므로 기본 summary는 bench.top_n 기준으로 다시 자른다.
    selected_rows = _rows_for_top_n(detail_rows, bench.top_n)
    trade_rows: List[Dict[str, Any]] = []
    trade_summary: Dict[str, Any] = {}
    if bench.trade_sim:
        trade_rows, trade_summary = _build_trade_rows_with_engine(
            selected_rows,
            full_raw,
            take_profit=bench.take_profit,
            stop_loss=bench.stop_loss,
            hold_days=bench.hold_days,
            same_day_rule=bench.same_day_rule,
        )
    if trade_summary:
        # v1.5 출력/CSV 컬럼명 호환 alias
        trade_summary["avg_trade_return"] = trade_summary.get("avg_return", np.nan)
        trade_summary["median_trade_return"] = trade_summary.get("median_return", np.nan)
        trade_summary["cum_return_equal_weight"] = trade_summary.get("cumulative_return", np.nan)
        trade_summary["mdd_trade"] = trade_summary.get("mdd", np.nan)
        trade_summary["profit_factor_trade"] = trade_summary.get("profit_factor", np.nan)
        trade_summary["take_profit_rate"] = trade_summary.get("tp_rate", np.nan)
        trade_summary["stop_loss_rate"] = trade_summary.get("sl_rate", np.nan)
        trade_summary["time_exit_rate"] = trade_summary.get("time_exit_rate", np.nan)

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
    summary["failed_dates"] = len(failed_dates)
    summary_df = pd.DataFrame([summary])

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(app_config.reports_dir, f"benchmark_{stamp}")
    os.makedirs(out_dir, exist_ok=True)

    print("[4/5] CSV/HTML 저장 중...")
    paths = {
        "summary": os.path.join(out_dir, "benchmark_summary.csv"),
        "detail": os.path.join(out_dir, "benchmark_detail.csv"),
        "ranked_detail": os.path.join(out_dir, "benchmark_ranked_detail.csv"),
        "candidates": os.path.join(out_dir, "benchmark_candidates.csv"),
        "top_list": os.path.join(out_dir, "benchmark_top_list.csv"),
        "random": os.path.join(out_dir, "benchmark_random_baseline.csv"),
        "alpha": os.path.join(out_dir, "benchmark_alpha.csv"),
        "trade": os.path.join(out_dir, "benchmark_trade_sim.csv"),
        "trade_summary": os.path.join(out_dir, "benchmark_trade_summary.csv"),
        "daily": os.path.join(out_dir, "benchmark_daily.csv"),
        "monthly": os.path.join(out_dir, "benchmark_monthly.csv"),
        "bucket": os.path.join(out_dir, "benchmark_score_buckets.csv"),
        "failed": os.path.join(out_dir, "benchmark_failed_dates.csv"),
        "html": os.path.join(out_dir, "benchmark_report.html"),
    }
    summary_df.to_csv(paths["summary"], index=False, encoding="utf-8-sig")
    detail_df.to_csv(paths["detail"], index=False, encoding="utf-8-sig")
    ranked_detail_df.to_csv(paths["ranked_detail"], index=False, encoding="utf-8-sig")
    candidate_df.to_csv(paths["candidates"], index=False, encoding="utf-8-sig")
    top_list_df.to_csv(paths["top_list"], index=False, encoding="utf-8-sig")
    random_df.to_csv(paths["random"], index=False, encoding="utf-8-sig")
    alpha_df.to_csv(paths["alpha"], index=False, encoding="utf-8-sig")
    trade_df.to_csv(paths["trade"], index=False, encoding="utf-8-sig")
    trade_summary_df.to_csv(paths["trade_summary"], index=False, encoding="utf-8-sig")
    daily_df.to_csv(paths["daily"], index=False, encoding="utf-8-sig")
    monthly_df.to_csv(paths["monthly"], index=False, encoding="utf-8-sig")
    bucket_df.to_csv(paths["bucket"], index=False, encoding="utf-8-sig")
    pd.DataFrame(failed_dates).to_csv(paths["failed"], index=False, encoding="utf-8-sig")
    _write_html_report(paths["html"], summary, daily_df, monthly_df, bucket_df, detail_df, top_list_df, random_df, alpha_df, trade_summary_df, trade_df)

    print("[5/5] 완료")
    return {"summary": summary, "paths": paths, "failed_dates": failed_dates, "top_list": top_list_df, "random": random_df, "alpha": alpha_df, "trade_summary": trade_summary_df, "trade": trade_df}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Phoenix Quant Benchmark v1.6")
    p.add_argument("--config", default="config/config.yaml", help="config.yaml 경로")
    p.add_argument("--start", required=True, help="시작일 YYYY-MM-DD")
    p.add_argument("--end", required=True, help="종료일 YYYY-MM-DD")
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
    p.add_argument("--trade-sim", action="store_true", help="실제 매매 시뮬레이션 실행: 기준일 종가 진입, TP/SL/시간청산")
    p.add_argument("--take-profit", type=float, default=0.05, help="익절 비율. 예: 0.05 = +5%")
    p.add_argument("--stop-loss", type=float, default=0.03, help="손절 비율. 예: 0.03 = -3%")
    p.add_argument("--hold-days", type=int, default=5, help="최대 보유 거래일 수")
    p.add_argument("--same-day-rule", choices=["stop_first", "take_first", "midpoint"], default="stop_first", help="일봉에서 익절/손절이 같은 날 모두 닿은 경우 처리 방식")
    return p


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    args = build_parser().parse_args()
    app_config = load_config(args.config)
    bench = BenchmarkConfig(
        start=args.start,
        end=args.end,
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
    )
    result = run_benchmark(app_config, bench)
    s = result["summary"]
    print()
    print("Phoenix Quant Benchmark v1.6")
    print("━━━━━━━━━━━━━━━━━━━━")
    print(f"기간: {s['start']} ~ {s['end']} / frequency={s['frequency']} / Top{s['top_n']}")
    print(f"기준일 수: {s['n_dates']} / 거래 수: {s['n_trades']} / 실패 기준일: {s['failed_dates']}")
    print(f"5D +5% Hit Rate: {_pct(s['hit_5pct_5d_rate'])}")
    print(f"10D +10% Hit Rate: {_pct(s['hit_10pct_10d_rate'])}")
    print(f"평균 5D 최대상승률: {_pct(s['avg_fwd_max_ret_5d'])}")
    print(f"평균 10D 최대상승률: {_pct(s['avg_fwd_max_ret_10d'])}")
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
            print(msg)
    trade_summary_df = result.get("trade_summary")
    if trade_summary_df is not None and not trade_summary_df.empty:
        tr = trade_summary_df.iloc[0]
        print()
        print("Trade Simulation:")
        print(f"- Rule: TP {_pct(tr.get('take_profit'))} / SL {_pct(tr.get('stop_loss'))} / Hold {int(tr.get('hold_days', 0))}D / same-day={tr.get('same_day_rule')}")
        print(f"- Win Rate: {_pct(tr.get('win_rate'))} / Avg Return: {_pct(tr.get('avg_trade_return'))} / Median: {_pct(tr.get('median_trade_return'))}")
        print(f"- Cum Return(eq-weight sequence): {_pct(tr.get('cum_return_equal_weight'))} / MDD: {_pct(tr.get('mdd_trade'))} / PF: {_fmt(tr.get('profit_factor_trade'))}")
        print(f"- TP Rate: {_pct(tr.get('take_profit_rate'))} / SL Rate: {_pct(tr.get('stop_loss_rate'))} / Time Exit: {_pct(tr.get('time_exit_rate'))}")
    print()
    print("저장 위치:")
    for name, path in result["paths"].items():
        print(f"- {name}: {path}")


if __name__ == "__main__":
    main()
