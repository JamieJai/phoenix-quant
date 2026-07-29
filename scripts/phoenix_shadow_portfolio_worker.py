#!/usr/bin/env python3
"""Advance the broker-free durable shadow portfolio from prospective rows."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from phoenix_core.trade.shadow_ledger import (  # noqa: E402
    ShadowBar,
    ShadowLedger,
    ShadowQuote,
    shadow_signal_id,
)

NY = ZoneInfo("America/New_York")
PREREG = ROOT / (
    "research/preregistrations/STATEFUL_SHADOW_PORTFOLIO_V1.json"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _timestamp(value: object) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if result.tzinfo is None:
        return None
    return result.astimezone(timezone.utc)


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


def completed_bars(
    ticker: str,
    frame: pd.DataFrame,
    *,
    now: datetime,
) -> list[ShadowBar]:
    frame = _flat(frame)
    required = {"Open", "High", "Low", "Close"}
    if frame.empty or not required.issubset(frame.columns):
        return []
    starts = pd.to_datetime(frame.index, utc=True, errors="coerce")
    available = starts + pd.Timedelta(minutes=1)
    rows: list[ShadowBar] = []
    for position in range(len(frame)):
        if pd.isna(starts[position]) or pd.isna(available[position]):
            continue
        available_at = available[position].to_pydatetime()
        if available_at > now:
            continue
        values = [
            pd.to_numeric(
                pd.Series([frame.iloc[position][column]]),
                errors="coerce",
            ).iloc[0]
            for column in ("Open", "High", "Low", "Close")
        ]
        if any(pd.isna(value) or not math.isfinite(float(value)) for value in values):
            continue
        rows.append(
            ShadowBar(
                ticker=ticker.upper(),
                bar_start=starts[position].to_pydatetime(),
                available_at=available_at,
                open=float(values[0]),
                high=float(values[1]),
                low=float(values[2]),
                close=float(values[3]),
            )
        )
    return rows


def _download(ticker: str) -> pd.DataFrame:
    import yfinance as yf

    return _flat(
        yf.download(
            ticker,
            period="5d",
            interval="1m",
            prepost=True,
            auto_adjust=False,
            progress=False,
            threads=False,
        )
    )


def _read_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        return pd.read_csv(path).to_dict(orient="records")


def run(
    *,
    cache: str,
    database: str,
    prospective_start: str,
    now: datetime | None = None,
) -> dict[str, object]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    ledger = ShadowLedger(ROOT / database)
    processed = ledger.processed_signal_ids()
    candidates: list[dict[str, object]] = []
    for row in _read_rows(ROOT / cache):
        recorded_at = _timestamp(row.get("recorded_at"))
        if (
            str(row.get("source", "")).lower() != "yfinance"
            or recorded_at is None
            or recorded_at.astimezone(NY).date().isoformat()
            < prospective_start
        ):
            continue
        if shadow_signal_id(row) not in processed:
            candidates.append(row)
    candidates.sort(
        key=lambda row: str(row.get("recorded_at", ""))
    )

    recent_tickers: set[str] = set()
    for row in candidates:
        recorded_at = _timestamp(row.get("recorded_at"))
        if (
            recorded_at is not None
            and 0
            <= (now - recorded_at).total_seconds()
            <= ledger.contract.maximum_signal_delay_seconds
        ):
            recent_tickers.add(str(row.get("ticker", "")).upper())
    requested = sorted(
        set(ledger.open_tickers()) | {ticker for ticker in recent_tickers if ticker}
    )
    frames: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    for ticker in requested:
        try:
            frames[ticker] = _download(ticker)
        except Exception as exc:
            frames[ticker] = pd.DataFrame()
            errors[ticker] = f"{type(exc).__name__}: {str(exc)[:160]}"

    exits: list[dict[str, object]] = []
    bars_by_ticker = {
        ticker: completed_bars(ticker, frame, now=now)
        for ticker, frame in frames.items()
    }
    for ticker in ledger.open_tickers():
        exits.extend(
            ledger.process_bars(
                ticker,
                bars_by_ticker.get(ticker, []),
                now=now,
            )
        )

    signal_results: list[dict[str, object]] = []
    for row in candidates:
        ticker = str(row.get("ticker", "")).upper()
        recorded_at = _timestamp(row.get("recorded_at"))
        bars = bars_by_ticker.get(ticker, [])
        if (
            recorded_at is None
            or (now - recorded_at).total_seconds()
            > ledger.contract.maximum_signal_delay_seconds
        ):
            quote = ShadowQuote(
                ticker=ticker,
                price=1.0,
                available_at=now - timedelta(
                    seconds=ledger.contract.maximum_quote_age_seconds + 1
                ),
            )
        elif bars:
            latest = bars[-1]
            quote = ShadowQuote(
                ticker=ticker,
                price=latest.close,
                available_at=latest.available_at,
            )
        else:
            quote = ShadowQuote(
                ticker=ticker,
                price=float("nan"),
                available_at=now,
            )
        signal_results.append(
            ledger.process_signal(row, quote, now=now)
        )

    snapshot = ledger.snapshot()
    status = (
        "DEGRADED"
        if errors
        else "SHADOW_ACTIVE"
        if requested or candidates or snapshot["positions"].get("OPEN", 0)
        else "WAITING_FOR_PROSPECTIVE_SIGNALS"
    )
    return {
        "status": status,
        "generated_at_utc": now.isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z"),
        "prospective_start": prospective_start,
        "new_candidates": len(candidates),
        "requested_tickers": requested,
        "fetch_errors": errors,
        "signal_results": signal_results,
        "exits": exits,
        "ledger": snapshot,
        "database": database,
        "preregistration": {
            "artifact": str(PREREG.relative_to(ROOT)),
            "sha256": sha256(PREREG),
        },
        "market_data_source": "YFINANCE_1M_ONLY",
        "network_used_for_market_data_only": bool(requested),
        "broker_routes_called": False,
        "account_endpoints_called": False,
        "live_enabled": False,
        "champion_changed": False,
        "production_score_changed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default="data/intraday_features.csv")
    parser.add_argument(
        "--database",
        default=(
            "data/research/paper_shadow_portfolio/"
            "shadow_portfolio_v1.sqlite3"
        ),
    )
    parser.add_argument("--prospective-start", default="2026-07-29")
    parser.add_argument(
        "--report",
        default=(
            "reports/paper_trading/shadow_ledger/"
            "shadow_portfolio_latest.json"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = run(
        cache=args.cache,
        database=args.database,
        prospective_start=args.prospective_start,
    )
    report = ROOT / args.report
    report.parent.mkdir(parents=True, exist_ok=True)
    temporary = report.with_name(f".{report.name}.tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, report)
    payload = {
        **result,
        "report": str(report.relative_to(ROOT)),
        "report_sha256": sha256(report),
    }
    print(
        json.dumps(payload, ensure_ascii=False)
        if args.json
        else (
            f"{result['status']} candidates={result['new_candidates']} "
            f"open={result['ledger']['positions'].get('OPEN', 0)}"
        )
    )
    return 2 if result["status"] == "DEGRADED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
