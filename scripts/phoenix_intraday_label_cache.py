#!/usr/bin/env python3
"""Fill matured paper labels without overwriting outcomes or mixing sources."""
from __future__ import annotations

import argparse
import fcntl
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phoenix_core.intraday_feature_store import (
    ensure_intraday_feature_cache_schema,
)


LABEL_META_COLUMNS = (
    "label_source_5m",
    "label_source_10m",
    "label_available_at_5m",
    "label_available_at_10m",
)


def _flat(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    result = frame.copy()
    if isinstance(result.columns, pd.MultiIndex):
        result.columns = [
            column[0] if isinstance(column, tuple) else column
            for column in result.columns
        ]
    return result


def label_from_bars(
    bars: pd.DataFrame,
    *,
    signal_timestamp: pd.Timestamp,
    signal_price: float,
    horizon_minutes: int,
    maximum_delay_minutes: int = 10,
) -> tuple[float, str] | None:
    if bars is None or bars.empty or "Close" not in bars.columns:
        return None
    starts = pd.to_datetime(bars.index, utc=True, errors="coerce")
    close = pd.to_numeric(bars["Close"], errors="coerce")
    available = starts + pd.Timedelta(minutes=5)
    target = signal_timestamp + pd.Timedelta(minutes=horizon_minutes)
    eligible = (
        (available >= target)
        & (available <= target + pd.Timedelta(minutes=maximum_delay_minutes))
        & close.notna()
        & (close > 0)
    )
    positions = [position for position, value in enumerate(eligible) if bool(value)]
    if not positions:
        return None
    position = positions[0]
    value = float(close.iloc[position] / signal_price - 1.0)
    return value, available[position].isoformat()


def _fetch_bars(ticker: str) -> pd.DataFrame:
    import yfinance as yf

    frame = yf.download(
        ticker,
        period="30d",
        interval="5m",
        prepost=True,
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    return _flat(frame)


def _cached_observation_labels(
    df: pd.DataFrame,
    *,
    prospective_start: str,
) -> int:
    ts = pd.to_datetime(df.get("timestamp"), utc=True, errors="coerce")
    px = pd.to_numeric(df.get("current_price"), errors="coerce")
    market_dates = ts.dt.tz_convert("America/New_York").dt.date.astype(str)
    filled = 0
    for _, idx in df.groupby(df["ticker"].astype(str).str.upper()).groups.items():
        order = sorted(
            idx,
            key=lambda i: (
                ts.iloc[i]
                if pd.notna(ts.iloc[i])
                else pd.Timestamp.max.tz_localize("UTC")
            ),
        )
        for i in order:
            if pd.isna(ts.iloc[i]) or pd.isna(px.iloc[i]) or px.iloc[i] == 0:
                continue
            # Legacy cache rows may retain the historical next-observation
            # labeler. Prospective rows are exclusively labeled from frozen,
            # source-isolated yfinance 5-minute bars below.
            if market_dates.iloc[i] >= prospective_start:
                continue
            for mins, ret, out in (
                (5, "forward_return_5m", "outcome_5m"),
                (10, "forward_return_10m", "outcome_10m"),
            ):
                if pd.notna(df.at[i, ret]):
                    continue
                target = ts.iloc[i] + pd.Timedelta(minutes=mins)
                future = next(
                    (
                        j
                        for j in order
                        if pd.notna(ts.iloc[j])
                        and target
                        <= ts.iloc[j]
                        <= target + pd.Timedelta(minutes=30)
                        and pd.notna(px.iloc[j])
                    ),
                    None,
                )
                if future is not None:
                    value = float(px.iloc[future] / px.iloc[i] - 1.0)
                    df.at[i, ret] = value
                    df.at[i, out] = int(value > 0)
                    filled += 1
    return filled


def _fetched_bar_labels(
    df: pd.DataFrame,
    *,
    prospective_start: str,
) -> tuple[int, dict[str, str]]:
    timestamps = pd.to_datetime(df.get("timestamp"), utc=True, errors="coerce")
    prices = pd.to_numeric(df.get("current_price"), errors="coerce")
    market_dates = timestamps.dt.tz_convert("America/New_York").dt.date.astype(str)
    candidate = (
        timestamps.notna()
        & prices.notna()
        & (prices > 0)
        & df["source"].astype(str).str.lower().eq("yfinance")
        & (market_dates >= prospective_start)
        & (
            df["forward_return_5m"].isna()
            | df["forward_return_10m"].isna()
        )
    )
    errors: dict[str, str] = {}
    fetched = 0
    bars_by_ticker: dict[str, pd.DataFrame] = {}
    for ticker in sorted(
        set(df.loc[candidate, "ticker"].astype(str).str.upper())
    ):
        try:
            bars_by_ticker[ticker] = _fetch_bars(ticker)
        except Exception as exc:
            errors[ticker] = f"{type(exc).__name__}: {str(exc)[:160]}"
            bars_by_ticker[ticker] = pd.DataFrame()
    for i in df.index[candidate]:
        ticker = str(df.at[i, "ticker"]).upper()
        bars = bars_by_ticker.get(ticker, pd.DataFrame())
        for horizon, ret, outcome in (
            (5, "forward_return_5m", "outcome_5m"),
            (10, "forward_return_10m", "outcome_10m"),
        ):
            if pd.notna(df.at[i, ret]):
                continue
            label = label_from_bars(
                bars,
                signal_timestamp=timestamps.iloc[i],
                signal_price=float(prices.iloc[i]),
                horizon_minutes=horizon,
            )
            if label is None:
                continue
            value, available_at = label
            df.at[i, ret] = value
            df.at[i, outcome] = int(value > 0)
            df.at[i, f"label_source_{horizon}m"] = "YFINANCE_5M_V2"
            df.at[i, f"label_available_at_{horizon}m"] = available_at
            fetched += 1
    return fetched, errors


def update(
    path: str,
    *,
    fetch_matured: bool = False,
    prospective_start: str = "2026-07-29",
) -> dict[str, object]:
    cache = Path(path)
    if not cache.exists():
        return {
            "rows": 0,
            "labeled_5m": 0,
            "labeled_10m": 0,
            "fetched_labels": 0,
            "fetch_errors": {},
        }
    ensure_intraday_feature_cache_schema(cache)
    lock_path = cache.with_suffix(cache.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        df = pd.read_csv(cache)
        for column in (
            "forward_return_5m",
            "forward_return_10m",
            "outcome_5m",
            "outcome_10m",
            *LABEL_META_COLUMNS,
        ):
            if column not in df:
                df[column] = pd.NA
        cached_labels = _cached_observation_labels(
            df,
            prospective_start=prospective_start,
        )
        fetched_labels = 0
        errors: dict[str, str] = {}
        if fetch_matured:
            fetched_labels, errors = _fetched_bar_labels(
                df,
                prospective_start=prospective_start,
            )
        temporary = cache.with_name(f".{cache.name}.labels.tmp")
        df.to_csv(temporary, index=False, lineterminator="\n")
        os.replace(temporary, cache)
    return {
        "rows": int(len(df)),
        "labeled_5m": int(df["forward_return_5m"].notna().sum()),
        "labeled_10m": int(df["forward_return_10m"].notna().sum()),
        "cached_observation_labels_added": cached_labels,
        "fetched_labels": fetched_labels,
        "fetch_errors": errors,
        "existing_labels_overwritten": False,
        "source_mixing": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default="data/intraday_features.csv")
    parser.add_argument("--fetch-matured", action="store_true")
    parser.add_argument("--prospective-start", default="2026-07-29")
    args = parser.parse_args()
    print(
        update(
            args.path,
            fetch_matured=args.fetch_matured,
            prospective_start=args.prospective_start,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
