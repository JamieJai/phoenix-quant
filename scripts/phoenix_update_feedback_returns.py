#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd


RETURN_FIELDS = {
    1: "observed_1d_return",
    5: "observed_5d_return",
    10: "observed_10d_return",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fill empty Phoenix operator feedback forward returns from cached daily OHLCV data."
    )
    parser.add_argument("--feedback-csv", default="data/operator_feedback.csv")
    parser.add_argument("--cache-dir", default="data")
    parser.add_argument("--output-csv", default=None, help="default: update --feedback-csv in place")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--require-full-horizon",
        action="store_true",
        help="only update a row when all 1D/5D/10D horizons are available",
    )
    return parser.parse_args()


def _read_feedback(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"feedback CSV has no header: {path}")
        return list(reader.fieldnames), list(reader)


def _load_close_series(cache_dir: Path, ticker: str) -> pd.Series | None:
    path = cache_dir / f"{ticker.upper()}.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["Date"])
    if df.empty or "Close" not in df.columns:
        return None
    df = df.sort_values("Date")
    close = pd.to_numeric(df["Close"], errors="coerce")
    series = pd.Series(close.to_numpy(), index=pd.to_datetime(df["Date"]).dt.date, name=ticker.upper())
    return series.dropna()


def _asof_index(series: pd.Series, as_of: date) -> int | None:
    dates = list(series.index)
    try:
        return dates.index(as_of)
    except ValueError:
        prior = [idx for idx, value in enumerate(dates) if value <= as_of]
        return prior[-1] if prior else None


def _forward_returns(series: pd.Series, as_of: date) -> dict[int, float]:
    idx = _asof_index(series, as_of)
    if idx is None:
        return {}
    base = float(series.iloc[idx])
    if base == 0:
        return {}
    out: dict[int, float] = {}
    for horizon in RETURN_FIELDS:
        fwd_idx = idx + horizon
        if fwd_idx < len(series):
            out[horizon] = float(series.iloc[fwd_idx] / base - 1.0)
    return out


def _fmt_return(value: float) -> str:
    return f"{value:.10g}"


def _write_feedback(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _parse_date(value: Any) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def main() -> int:
    args = _parse_args()
    feedback_path = Path(args.feedback_csv)
    output_path = Path(args.output_csv) if args.output_csv else feedback_path
    cache_dir = Path(args.cache_dir)

    try:
        fieldnames, rows = _read_feedback(feedback_path)
        missing = [field for field in RETURN_FIELDS.values() if field not in fieldnames]
        if missing:
            raise ValueError(f"feedback CSV missing return fields: {missing}")

        close_cache: dict[str, pd.Series | None] = {}
        updated_cells = 0
        updated_rows = 0
        unavailable_rows = 0

        for row in rows:
            ticker = str(row.get("ticker") or "").strip().upper()
            as_of = _parse_date(row.get("as_of_date"))
            if not ticker or as_of is None:
                unavailable_rows += 1
                continue
            if ticker not in close_cache:
                close_cache[ticker] = _load_close_series(cache_dir, ticker)
            series = close_cache[ticker]
            if series is None or series.empty:
                unavailable_rows += 1
                continue
            returns = _forward_returns(series, as_of)
            if args.require_full_horizon and any(h not in returns for h in RETURN_FIELDS):
                unavailable_rows += 1
                continue

            row_updates = 0
            for horizon, field in RETURN_FIELDS.items():
                if str(row.get(field) or "").strip():
                    continue
                if horizon not in returns:
                    continue
                row[field] = _fmt_return(returns[horizon])
                row_updates += 1
            if row_updates:
                updated_rows += 1
                updated_cells += row_updates

        print(
            f"feedback_rows={len(rows)} updated_rows={updated_rows} "
            f"updated_cells={updated_cells} unavailable_rows={unavailable_rows}"
        )
        if not args.dry_run and updated_cells:
            _write_feedback(output_path, fieldnames, rows)
            print(f"UPDATED {output_path}")
        elif args.dry_run:
            print("DRY_RUN no file written")
        else:
            print("NO_UPDATES")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
