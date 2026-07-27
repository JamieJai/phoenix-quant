#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from phoenix_core.config import load_config
from phoenix_core.data_loader import normalize_ohlcv


@dataclass(frozen=True)
class SplitSpec:
    name: str
    train_start: str
    train_end: str
    test_start: str
    test_end: str


@dataclass(frozen=True)
class TickerCoverage:
    ticker: str
    source: str
    csv_path: str
    exists: bool
    rows: int
    first_date: str
    latest_date: str
    age_days: int
    stale: bool
    min_rows_ok: bool
    reason: str


@dataclass(frozen=True)
class SplitCoverage:
    split: str
    phase: str
    ticker: str
    source: str
    start: str
    end: str
    required_trading_days: int
    available_rows: int
    coverage_ratio: float
    first_date: str
    latest_date: str
    usable: bool
    reason: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit cached Phoenix daily data coverage by ticker and train/test split.")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--include-etfs", action="store_true", help="include market ETFs in addition to config.universe")
    parser.add_argument("--ticker", action="append", default=[], help="extra ticker to audit; can be repeated")
    parser.add_argument("--min-total-rows", type=int, default=500)
    parser.add_argument("--max-age-days", type=int, default=7)
    parser.add_argument("--min-split-coverage", type=float, default=0.80)
    parser.add_argument(
        "--min-universe-usable-ratio",
        type=float,
        default=0.90,
        help="minimum usable ticker ratio required for every train/test phase",
    )
    parser.add_argument(
        "--fail-on-issues",
        action="store_true",
        help="exit 2 when missing/stale data or split coverage fails",
    )
    parser.add_argument("--split", action="append", default=[], help="name:train_start,train_end,test_start,test_end; can be repeated")
    parser.add_argument("--json", action="store_true", help="print summary JSON only")
    return parser.parse_args()


def _dedupe(tickers: Iterable[str]) -> list[str]:
    out: list[str] = []
    for ticker in tickers:
        ticker = str(ticker).strip()
        if not ticker:
            continue
        out.append(ticker if ticker.startswith("^") else ticker.upper())
    return list(dict.fromkeys(out))


def _cache_path(ticker: str, cache_dir: str) -> Path:
    safe = ticker.replace("^", "IDX_").replace("/", "_")
    return Path(cache_dir) / f"{safe}.csv"


def _read_cached_ohlcv(ticker: str, cache_dir: str) -> tuple[pd.DataFrame | None, Path, str | None]:
    path = _cache_path(ticker, cache_dir)
    if not path.exists():
        return None, path, "missing_csv"
    try:
        df = normalize_ohlcv(pd.read_csv(path, index_col="Date", parse_dates=True))
    except Exception as exc:  # noqa: BLE001
        return None, path, f"invalid_csv:{exc}"
    return df, path, None


def _split_chunks(raw: str) -> list[str]:
    return [part.strip() for part in str(raw or "").split(";") if part.strip()]


def _parse_split(raw: str, idx: int) -> SplitSpec:
    name = f"split_{idx:02d}"
    body = raw
    if ":" in raw:
        maybe_name, body = raw.split(":", 1)
        if maybe_name.strip():
            name = maybe_name.strip()
    parts = [part.strip() for part in body.split(",")]
    if len(parts) != 4:
        raise ValueError(f"Invalid split {raw!r}; expected name:train_start,train_end,test_start,test_end")
    return SplitSpec(name=name, train_start=parts[0], train_end=parts[1], test_start=parts[2], test_end=parts[3])


def _build_splits(args: argparse.Namespace) -> list[SplitSpec]:
    raw_splits: list[str] = []
    raw_splits.extend(args.split or [])

    env_rolling = os.getenv("PHOENIX_ROLLING_SPLITS", "")
    raw_splits.extend(_split_chunks(env_rolling))

    main_dates = (
        os.getenv("PHOENIX_TRAIN_START", "2023-01-01"),
        os.getenv("PHOENIX_TRAIN_END", "2024-12-20"),
        os.getenv("PHOENIX_TEST_START", "2025-01-16"),
        os.getenv("PHOENIX_TEST_END", "2026-07-06"),
    )
    raw_splits.append(f"main:{','.join(main_dates)}")

    out: list[SplitSpec] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for idx, raw in enumerate(raw_splits, start=1):
        split = _parse_split(raw, idx)
        key = (split.name, split.train_start, split.train_end, split.test_start, split.test_end)
        if key in seen:
            continue
        seen.add(key)
        out.append(split)
    return out


def _trading_days(spy: pd.DataFrame | None, start: str, end: str) -> pd.DatetimeIndex:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if spy is not None and not spy.empty:
        idx = pd.DatetimeIndex(spy.index).sort_values()
        if len(idx) and idx.min() <= start_ts and idx.max() >= end_ts:
            return idx[(idx >= start_ts) & (idx <= end_ts)]
    # If the SPY cache itself starts late, do not let it hide missing history.
    # Business days slightly over-count holidays, but they preserve the requested window.
    return pd.bdate_range(start_ts, end_ts)


def _coverage_row(
    *,
    split_name: str,
    phase: str,
    ticker: str,
    source: str,
    df: pd.DataFrame | None,
    required_days: pd.DatetimeIndex,
    min_coverage: float,
) -> SplitCoverage:
    start = required_days.min().date().isoformat() if len(required_days) else ""
    end = required_days.max().date().isoformat() if len(required_days) else ""
    first = ""
    latest = ""
    available = 0
    ratio = 0.0
    reasons: list[str] = []

    if df is None or df.empty:
        reasons.append("missing")
    elif len(required_days) == 0:
        reasons.append("no_required_days")
    else:
        idx = pd.DatetimeIndex(df.index).sort_values()
        first = idx.min().date().isoformat()
        latest = idx.max().date().isoformat()
        available = int(idx.intersection(required_days).nunique())
        ratio = available / len(required_days) if len(required_days) else 0.0
        if idx.min() > required_days.min():
            reasons.append("starts_after_window")
        if idx.max() < required_days.max():
            reasons.append("ends_before_window")
        if ratio < min_coverage:
            reasons.append(f"coverage<{min_coverage:.2f}")

    return SplitCoverage(
        split=split_name,
        phase=phase,
        ticker=ticker,
        source=source,
        start=start,
        end=end,
        required_trading_days=int(len(required_days)),
        available_rows=int(available),
        coverage_ratio=float(ratio),
        first_date=first,
        latest_date=latest,
        usable=not reasons,
        reason=";".join(reasons) if reasons else "ok",
    )


def _ticker_row(ticker: str, source: str, path: Path, df: pd.DataFrame | None, error: str | None, min_rows: int, max_age_days: int) -> TickerCoverage:
    if df is None or df.empty:
        return TickerCoverage(ticker, source, str(path), path.exists(), 0, "", "", 999999, True, False, error or "missing")
    idx = pd.DatetimeIndex(df.index).sort_values()
    latest = idx.max().date()
    first = idx.min().date()
    age = (date.today() - latest).days
    reasons: list[str] = []
    if len(df) < min_rows:
        reasons.append(f"rows<{min_rows}")
    if age > max_age_days:
        reasons.append(f"age>{max_age_days}d")
    return TickerCoverage(
        ticker=ticker,
        source=source,
        csv_path=str(path),
        exists=True,
        rows=int(len(df)),
        first_date=first.isoformat(),
        latest_date=latest.isoformat(),
        age_days=int(age),
        stale=age > max_age_days,
        min_rows_ok=len(df) >= min_rows,
        reason=";".join(reasons) if reasons else "ok",
    )


def _write_csv(path: Path, rows: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def _pct(value: float) -> str:
    if not math.isfinite(value):
        return "n/a"
    return f"{value * 100:.1f}%"


def main() -> int:
    args = _parse_args()
    config = load_config(args.config)
    cache_dir = args.cache_dir or config.cache_dir
    tickers = list(config.universe)
    sources = {ticker.upper(): "universe" for ticker in config.universe}
    if args.include_etfs:
        tickers.extend(config.market_etfs)
        for ticker in config.market_etfs:
            sources[ticker if ticker.startswith("^") else ticker.upper()] = "market_etf"
    tickers.extend(args.ticker)
    for ticker in args.ticker:
        sources[ticker if str(ticker).startswith("^") else str(ticker).upper()] = "extra"
    tickers = _dedupe(tickers)
    splits = _build_splits(args)

    raw: dict[str, pd.DataFrame | None] = {}
    ticker_rows: list[TickerCoverage] = []
    for ticker in tickers:
        df, path, error = _read_cached_ohlcv(ticker, cache_dir)
        raw[ticker] = df
        ticker_rows.append(_ticker_row(ticker, sources.get(ticker, "universe"), path, df, error, args.min_total_rows, args.max_age_days))

    spy, _spy_path, _spy_error = _read_cached_ohlcv("SPY", cache_dir)
    split_rows: list[SplitCoverage] = []
    for split in splits:
        phase_ranges = [("train", split.train_start, split.train_end), ("test", split.test_start, split.test_end)]
        for phase, start, end in phase_ranges:
            required_days = _trading_days(spy, start, end)
            for ticker in tickers:
                split_rows.append(_coverage_row(
                    split_name=split.name,
                    phase=phase,
                    ticker=ticker,
                    source=sources.get(ticker, "universe"),
                    df=raw.get(ticker),
                    required_days=required_days,
                    min_coverage=args.min_split_coverage,
                ))

    now_utc = datetime.now(timezone.utc)
    stamp = now_utc.strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir or Path("reports") / "data_coverage" / stamp)
    ticker_csv = output_dir / "ticker_coverage.csv"
    split_csv = output_dir / "split_coverage.csv"
    _write_csv(ticker_csv, ticker_rows)
    _write_csv(split_csv, split_rows)

    split_summary: list[dict[str, Any]] = []
    for split in splits:
        for phase in ["train", "test"]:
            rows = [row for row in split_rows if row.split == split.name and row.phase == phase]
            usable = [row for row in rows if row.usable]
            reasons: dict[str, int] = {}
            for row in rows:
                if row.usable:
                    continue
                for reason in row.reason.split(";"):
                    reasons[reason] = reasons.get(reason, 0) + 1
            split_summary.append({
                "split": split.name,
                "phase": phase,
                "start": rows[0].start if rows else "",
                "end": rows[0].end if rows else "",
                "required_trading_days": rows[0].required_trading_days if rows else 0,
                "usable_tickers": len(usable),
                "total_tickers": len(rows),
                "usable_ratio": len(usable) / len(rows) if rows else 0.0,
                "top_failure_reasons": sorted(reasons.items(), key=lambda item: (-item[1], item[0]))[:5],
            })

    stale_count = sum(1 for row in ticker_rows if row.stale)
    short_count = sum(1 for row in ticker_rows if not row.min_rows_ok)
    missing_count = sum(1 for row in ticker_rows if not row.exists)
    failure_reasons: list[str] = []
    if missing_count:
        failure_reasons.append(f"missing_csv_count={missing_count}")
    if stale_count:
        failure_reasons.append(f"stale_count={stale_count}")
    for item in split_summary:
        if item["usable_ratio"] < args.min_universe_usable_ratio:
            failure_reasons.append(
                f"{item['split']}:{item['phase']}_usable_ratio="
                f"{item['usable_ratio']:.6f}<{args.min_universe_usable_ratio:.6f}"
            )
    summary = {
        "generated_at": now_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "cache_dir": cache_dir,
        "ticker_count": len(ticker_rows),
        "missing_csv_count": missing_count,
        "stale_count": stale_count,
        "short_history_count": short_count,
        "min_total_rows": args.min_total_rows,
        "max_age_days": args.max_age_days,
        "min_split_coverage": args.min_split_coverage,
        "min_universe_usable_ratio": args.min_universe_usable_ratio,
        "passed": not failure_reasons,
        "failure_reasons": failure_reasons,
        "ticker_csv": str(ticker_csv),
        "split_csv": str(split_csv),
        "splits": split_summary,
    }
    summary_json = output_dir / "summary.json"
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("Phoenix Data Coverage Audit")
        print("===========================")
        print(f"tickers={len(ticker_rows)} missing={missing_count} short_history={short_count} stale={stale_count}")
        print(f"output={output_dir}")
        print()
        print("Split coverage:")
        for item in split_summary:
            print(
                f"- {item['split']} {item['phase']} {item['start']}..{item['end']}: "
                f"usable {item['usable_tickers']}/{item['total_tickers']} ({_pct(item['usable_ratio'])}) "
                f"required_days={item['required_trading_days']}"
            )
            if item["top_failure_reasons"]:
                reasons = ", ".join(f"{name}={count}" for name, count in item["top_failure_reasons"])
                print(f"  failures: {reasons}")
        weak = [row for row in ticker_rows if row.reason != "ok"][:15]
        if weak:
            print()
            print("Ticker issues:")
            for row in weak:
                print(f"- {row.ticker}: {row.reason} rows={row.rows} first={row.first_date or '-'} latest={row.latest_date or '-'}")
    if args.fail_on_issues and failure_reasons:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
