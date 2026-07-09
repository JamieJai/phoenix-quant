from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from phoenix_core.config import load_config
from phoenix_core.data_loader import download_ohlcv


@dataclass(frozen=True)
class DataQualityRow:
    ticker: str
    rows: int
    first_date: str
    latest_date: str
    age_days: int
    latest_close: float
    latest_volume: float
    ok: bool
    reason: str


def _dedupe(tickers: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for ticker in tickers:
        ticker = str(ticker).strip()
        if not ticker:
            continue
        normalized.append(ticker if ticker.startswith("^") else ticker.upper())
    return list(dict.fromkeys(normalized))


def _quality_row(ticker: str, df: pd.DataFrame | None, min_rows: int, max_age_days: int) -> DataQualityRow:
    if df is None or df.empty:
        return DataQualityRow(ticker, 0, "", "", 999999, 0.0, 0.0, False, "missing")

    latest_ts = pd.Timestamp(df.index.max())
    first_ts = pd.Timestamp(df.index.min())
    age_days = (date.today() - latest_ts.date()).days
    rows = len(df)
    reasons: list[str] = []
    if rows < min_rows:
        reasons.append(f"rows<{min_rows}")
    if age_days > max_age_days:
        reasons.append(f"age>{max_age_days}d")

    latest = df.loc[latest_ts]
    return DataQualityRow(
        ticker=ticker,
        rows=rows,
        first_date=first_ts.date().isoformat(),
        latest_date=latest_ts.date().isoformat(),
        age_days=age_days,
        latest_close=float(latest["Close"]),
        latest_volume=float(latest["Volume"]),
        ok=not reasons,
        reason=",".join(reasons) if reasons else "ok",
    )


def _write_manifest(path: str, rows: list[DataQualityRow]) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(DataQualityRow.__dataclass_fields__.keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)
    os.replace(tmp_path, path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download and validate daily OHLCV cache for Phoenix training.")
    parser.add_argument("--config", default="config/config.yaml", help="config.yaml path")
    parser.add_argument("--period", default="5y", help="yfinance period, e.g. 3y, 5y, 10y, max")
    parser.add_argument("--cache-dir", default=None, help="override cache directory")
    parser.add_argument("--refresh", action="store_true", help="ignore existing CSV cache and redownload")
    parser.add_argument("--universe-only", action="store_true", help="download config.universe only, excluding market ETFs")
    parser.add_argument("--ticker", action="append", default=[], help="extra ticker to include; can be repeated")
    parser.add_argument("--min-rows", type=int, default=300, help="minimum valid daily rows per ticker")
    parser.add_argument("--max-age-days", type=int, default=7, help="maximum age of latest daily bar")
    parser.add_argument("--manifest", default=None, help="output CSV manifest path")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config)
    cache_dir = args.cache_dir or config.cache_dir
    tickers = list(config.universe)
    if not args.universe_only:
        tickers.extend(config.market_etfs)
    tickers.extend(args.ticker)
    tickers = _dedupe(tickers)

    print(f"downloading daily data: tickers={len(tickers)} period={args.period} cache_dir={cache_dir} refresh={args.refresh}")
    raw = download_ohlcv(tickers, cache_dir=cache_dir, period=args.period, interval="1d", force_refresh=args.refresh)

    rows = [_quality_row(ticker, raw.get(ticker), args.min_rows, args.max_age_days) for ticker in tickers]
    ok_rows = [row for row in rows if row.ok]
    bad_rows = [row for row in rows if not row.ok]
    latest_dates = sorted({row.latest_date for row in ok_rows if row.latest_date})

    manifest = args.manifest or os.path.join(cache_dir, "daily_data_manifest.csv")
    _write_manifest(manifest, rows)

    print(f"success={len(ok_rows)} failed_or_stale={len(bad_rows)} manifest={manifest}")
    if latest_dates:
        print(f"latest_date_range={latest_dates[0]}..{latest_dates[-1]}")
    if bad_rows:
        print("bad tickers:")
        for row in bad_rows:
            print(f"  {row.ticker}: {row.reason} rows={row.rows} latest={row.latest_date or '-'}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
