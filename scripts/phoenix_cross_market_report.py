#!/usr/bin/env python3
"""Research-only cross-market context report for Phoenix Quant.

Reads cached OHLCV files and computes point-in-time trend/return/correlation
observations.  This script deliberately does not alter models, weights, or
deployment state; all values are uncalibrated research diagnostics.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from phoenix_core.config import load_config
from phoenix_core.data_loader import normalize_ohlcv


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate research-only cross-market context report")
    p.add_argument("--config", default="config/config.yaml")
    p.add_argument("--cache-dir", default=None)
    p.add_argument("--ticker", action="append", default=[])
    p.add_argument("--as-of", default=None, help="ISO date; defaults to latest common cached date")
    p.add_argument("--json", action="store_true")
    return p.parse_args()


def _path(ticker: str, cache: Path) -> Path:
    return cache / f"{ticker.replace('^', 'IDX_').replace('/', '_')}.csv"


def _load(ticker: str, cache: Path) -> pd.DataFrame | None:
    path = _path(ticker, cache)
    if not path.exists():
        return None
    try:
        return normalize_ohlcv(pd.read_csv(path, index_col="Date", parse_dates=True))
    except Exception:
        return None


def _trend(df: pd.DataFrame, window: int = 20) -> float:
    d = df.sort_index().dropna(subset=["Close"])
    if len(d) < window + 1:
        return 0.0
    ma = d["Close"].rolling(window).mean().iloc[-1]
    return float((d["Close"].iloc[-1] - ma) / ma) if ma else 0.0


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    cfg = load_config(args.config)
    cache = Path(args.cache_dir or cfg.cache_dir)
    tickers = list(dict.fromkeys([*(cfg.market_etfs or []), "SPY", "QQQ", "^VIX", "SOXX", "SMH", *args.ticker]))
    frames = {t: _load(t, cache) for t in tickers}
    usable = {t: d for t, d in frames.items() if d is not None and not d.empty}
    if args.as_of:
        asof = pd.Timestamp(args.as_of)
    elif usable:
        asof = min(pd.Timestamp(d.index.max()) for d in usable.values())
    else:
        asof = pd.Timestamp(date.today())
    asof_s = asof.date().isoformat()
    rows: dict[str, Any] = {}
    for t, df in frames.items():
        if df is None:
            rows[t] = {"available": False}
            continue
        d = df[df.index <= asof].sort_index()
        close = d["Close"].dropna()
        def ret(n: int) -> float | None:
            return float(close.iloc[-1] / close.iloc[-n-1] - 1.0) if len(close) > n else None
        rows[t] = {"available": bool(len(close)), "latest": close.index[-1].date().isoformat() if len(close) else None,
                   "close": float(close.iloc[-1]) if len(close) else None, "trend_20d": _trend(d),
                   "return_5d": ret(5), "return_20d": ret(20)}
    close_df = pd.DataFrame({t: d[d.index <= asof]["Close"] for t, d in frames.items() if d is not None}).pct_change().tail(180)
    correlations = close_df.corr(min_periods=30).round(4).to_dict() if not close_df.empty else {}
    return {"status": "EXPERIMENTAL", "research_only": True, "calibrated": False, "as_of": asof_s,
            "tickers": rows, "return_correlation_180d": correlations,
            "notes": ["Observations are descriptive and point-in-time; no production score or trading instruction."]}


def main() -> int:
    report = build_report(_args())
    print(json.dumps(report, ensure_ascii=False, indent=None if _args().json else 2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
