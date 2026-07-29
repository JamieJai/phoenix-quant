#!/usr/bin/env python3
"""Build a research-only Toss US capability dataset and source comparison.

This script never imports Toss data into Phoenix production paths. It calls
market-data endpoints only, keeps raw and normalized artifacts separate, and
records source provenance and SHA256 checksums.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PYTHON_STOCK_ROOT = Path("/home/sysadmin/python-stock")
DEFAULT_TICKERS = ("AAPL", "NVDA", "QQQ", "SMH", "SOXX", "AMD", "AVGO", "TSM")
DEFAULT_MARKET_DATE = "2026-07-24"
DEFAULT_DST_DATES = ("2026-03-06", "2026-03-13")
SOURCE_ROLE = "SECONDARY_RESEARCH_SOURCE"
TOSS_SOURCE = "toss_openapi"
YFINANCE_SOURCE = "yfinance"
OPENAPI_URL = "https://openapi.tossinvest.com/openapi-docs/latest/openapi.json"
NY = "America/New_York"
KST = "Asia/Seoul"
UTC = "UTC"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def relative(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-stock-root", default=str(DEFAULT_PYTHON_STOCK_ROOT))
    parser.add_argument("--output-root", default="data/research/toss_us")
    parser.add_argument("--report-root", default="reports/research/toss_us")
    parser.add_argument("--market-date", default=DEFAULT_MARKET_DATE)
    parser.add_argument("--dst-dates", default=",".join(DEFAULT_DST_DATES))
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--max-retries", type=int, default=8)
    parser.add_argument("--request-spacing", type=float, default=0.25)
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser.parse_args()


class ResearchMarketDataClient:
    """Research-only client using the repository's authorized OAuth settings."""

    def __init__(
        self,
        python_stock_root: Path,
        *,
        timeout: float,
        max_retries: int,
        request_spacing: float,
    ) -> None:
        load_dotenv(python_stock_root / ".env")
        client_id = os.getenv("TOSS_INVEST_CLIENT_ID", "").strip()
        client_secret = os.getenv("TOSS_INVEST_CLIENT_SECRET", "").strip()
        access_token = os.getenv("TOSS_INVEST_ACCESS_TOKEN", "").strip()
        if not client_id or not client_secret:
            raise RuntimeError("authorized Toss client credentials are not configured")
        self.base_url = (
            os.getenv("TOSS_INVEST_BASE_URL", "").strip()
            or "https://openapi.tossinvest.com"
        ).rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.timeout = timeout
        self.max_retries = max_retries
        self.request_spacing = max(0.0, request_spacing)
        self.request_count = 0
        self.rate_limit_retries = 0
        self.retry_after_seconds = 0.0

    def ensure_token(self) -> None:
        if self.access_token:
            return
        response = requests.post(
            f"{self.base_url}/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not token:
            raise RuntimeError("Toss token response did not include an access token")
        self.access_token = str(token)

    def get(self, path: str, params: dict[str, object]) -> dict[str, Any]:
        self.ensure_token()
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.get(
                    f"{self.base_url}{path}",
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    params=params,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                payload = response.json()
                self.request_count += 1
                if self.request_spacing:
                    time.sleep(self.request_spacing)
                return payload
            except requests.HTTPError as exc:
                self.request_count += 1
                response = exc.response
                if response is None or response.status_code != 429 or attempt >= self.max_retries:
                    raise
                header = response.headers.get("Retry-After") or response.headers.get(
                    "X-RateLimit-Reset", "1"
                )
                try:
                    wait_seconds = max(1.0, min(10.0, float(header)))
                except (TypeError, ValueError):
                    wait_seconds = 1.0
                self.rate_limit_retries += 1
                self.retry_after_seconds += wait_seconds
                time.sleep(wait_seconds)
        raise AssertionError("unreachable")


def payload_rows(payload: dict[str, Any], key: str = "candles") -> list[dict[str, Any]]:
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    rows = result.get(key)
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def payload_next_before(payload: dict[str, Any]) -> str | None:
    result = payload.get("result")
    value = result.get("nextBefore") if isinstance(result, dict) else None
    return str(value) if value else None


def calendar_day(payload: dict[str, Any], requested_date: str) -> dict[str, Any]:
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("US market calendar result is missing")
    candidates = [
        value
        for value in result.values()
        if isinstance(value, dict) and value.get("date") == requested_date
    ]
    if not candidates:
        raise RuntimeError(f"US market calendar does not contain {requested_date}")
    return candidates[0]


def session_bounds(day: dict[str, Any]) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    output: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {}
    for key, name in (
        ("dayMarket", "DAY_MARKET"),
        ("preMarket", "PREMARKET"),
        ("regularMarket", "REGULAR"),
        ("afterMarket", "AFTER_HOURS"),
    ):
        value = day.get(key)
        if not isinstance(value, dict):
            continue
        start = pd.Timestamp(value["startTime"])
        end = pd.Timestamp(value["endTime"])
        output[name] = (start, end)
    if not output:
        raise RuntimeError("US market calendar has no active sessions")
    return output


def classify_session(
    timestamp: pd.Timestamp,
    bounds: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
) -> str:
    for name, (start, end) in bounds.items():
        if start <= timestamp < end:
            return name
    return "OUTSIDE_CALENDAR"


def normalize_toss_rows(
    rows: list[dict[str, Any]],
    *,
    ticker: str,
    market_date: str,
    interval: str,
    bounds: dict[str, tuple[pd.Timestamp, pd.Timestamp]] | None,
    downloaded_at: str,
    raw_path: str,
    raw_digest: str,
) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for row in rows:
        source_timestamp = pd.Timestamp(row.get("timestamp"))
        if source_timestamp.tzinfo is None:
            raise ValueError("Toss timestamp is missing a timezone offset")
        source_timestamp_utc = source_timestamp.tz_convert(UTC)
        if interval == "1m":
            timestamp_utc = source_timestamp_utc - pd.Timedelta(minutes=1)
            available_at_utc = source_timestamp_utc
            observed_semantics = "BAR_END_LABEL_EMPIRICAL"
        else:
            timestamp_utc = source_timestamp_utc
            available_at_utc = source_timestamp_utc + pd.Timedelta(days=1)
            observed_semantics = "BAR_START_DOCUMENTED"
        timestamp_kst = timestamp_utc.tz_convert(KST)
        timestamp_ny = timestamp_utc.tz_convert(NY)
        session = classify_session(timestamp_kst, bounds) if bounds else "DAILY"
        records.append(
            {
                "source": TOSS_SOURCE,
                "source_role": SOURCE_ROLE,
                "ticker": ticker,
                "market_date": market_date,
                "interval": interval,
                "bar_timestamp_semantics": "BAR_START",
                "source_timestamp": source_timestamp.isoformat(),
                "source_timestamp_documented_semantics": "BAR_START",
                "source_timestamp_observed_semantics": observed_semantics,
                "bar_start_utc": timestamp_utc.isoformat(),
                "bar_end_utc": (
                    source_timestamp_utc.isoformat() if interval == "1m" else None
                ),
                "bar_available_at_utc": available_at_utc.isoformat(),
                "bar_start_ny": timestamp_ny.isoformat(),
                "bar_start_kst": timestamp_kst.isoformat(),
                "session": session,
                "open": pd.to_numeric(row.get("openPrice"), errors="coerce"),
                "high": pd.to_numeric(row.get("highPrice"), errors="coerce"),
                "low": pd.to_numeric(row.get("lowPrice"), errors="coerce"),
                "close": pd.to_numeric(row.get("closePrice"), errors="coerce"),
                "volume": pd.to_numeric(row.get("volume"), errors="coerce"),
                "currency": row.get("currency"),
                "trade_value": math.nan,
                "downloaded_at_utc": downloaded_at,
                "raw_artifact": raw_path,
                "raw_sha256": raw_digest,
            }
        )
    if not records:
        return pd.DataFrame()
    frame = pd.DataFrame(records)
    frame = frame.dropna(subset=["bar_start_utc", "open", "high", "low", "close", "volume"])
    return frame.sort_values("bar_start_utc").drop_duplicates("bar_start_utc", keep="last")


def collect_minute_session(
    client: ResearchMarketDataClient,
    *,
    ticker: str,
    market_date: str,
    bounds: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
    page_size: int,
    max_pages: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    overall_start = min(start for start, _ in bounds.values())
    overall_end = max(end for _, end in bounds.values())
    cursor = overall_end.isoformat()
    pages: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    for page_number in range(1, max_pages + 1):
        payload = client.get(
            "/api/v1/candles",
            {
                "symbol": ticker,
                "interval": "1m",
                "count": page_size,
                "before": cursor,
                "adjusted": "false",
            },
        )
        rows = payload_rows(payload)
        pages.append(
            {
                "page": page_number,
                "before": cursor,
                "nextBefore": payload_next_before(payload),
                "row_count": len(rows),
                "response": payload,
            }
        )
        if not rows:
            break
        all_rows.extend(rows)
        timestamps = pd.to_datetime(
            pd.Series([row.get("timestamp") for row in rows]), errors="coerce", utc=True
        ).dropna()
        if timestamps.empty or timestamps.min() <= overall_start.tz_convert(UTC):
            break
        next_before = payload_next_before(payload)
        if not next_before or next_before == cursor:
            break
        cursor = next_before
    kept = []
    for row in all_rows:
        timestamp = pd.Timestamp(row.get("timestamp"))
        if timestamp.tzinfo is not None and overall_start < timestamp.tz_convert(KST) <= overall_end:
            kept.append(row)
    return pages, kept


def session_coverage(
    frame: pd.DataFrame,
    bounds: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
) -> dict[str, dict[str, object]]:
    output: dict[str, dict[str, object]] = {}
    for name, (start, end) in bounds.items():
        expected = int((end - start).total_seconds() // 60)
        actual = int((frame["session"] == name).sum()) if not frame.empty else 0
        output[name] = {
            "expected_minutes": expected,
            "observed_bars": actual,
            "missing_bars": max(0, expected - actual),
            "missing_bar_rate": round(max(0, expected - actual) / expected, 6)
            if expected
            else None,
        }
    return output


def flatten_yfinance(frame: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    output = frame.copy()
    if isinstance(output.columns, pd.MultiIndex):
        if ticker in output.columns.get_level_values(-1):
            output = output.xs(ticker, axis=1, level=-1)
        else:
            output.columns = output.columns.get_level_values(0)
    output = output.rename(columns={str(column): str(column).lower() for column in output.columns})
    output.index = pd.to_datetime(output.index)
    if output.index.tz is None:
        output.index = output.index.tz_localize(NY)
    return output


def normalize_yfinance_minute(
    frame: pd.DataFrame,
    ticker: str,
    market_date: str,
    bounds: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    records = frame.reset_index()
    timestamp_column = records.columns[0]
    timestamps = pd.to_datetime(records[timestamp_column], utc=True)
    timestamps_kst = timestamps.dt.tz_convert(KST)
    output = pd.DataFrame(
        {
            "source": YFINANCE_SOURCE,
            "source_role": "CANONICAL_COMPARISON_SOURCE",
            "ticker": ticker,
            "market_date": market_date,
            "interval": "1m",
            "bar_timestamp_semantics": "BAR_START",
            "bar_start_utc": timestamps.map(lambda value: value.isoformat()),
            "bar_start_ny": timestamps.dt.tz_convert(NY).map(lambda value: value.isoformat()),
            "session": timestamps_kst.map(lambda value: classify_session(value, bounds)),
            "open": pd.to_numeric(records["open"], errors="coerce"),
            "high": pd.to_numeric(records["high"], errors="coerce"),
            "low": pd.to_numeric(records["low"], errors="coerce"),
            "close": pd.to_numeric(records["close"], errors="coerce"),
            "volume": pd.to_numeric(records["volume"], errors="coerce"),
        }
    )
    return output.dropna(subset=["bar_start_utc", "open", "high", "low", "close", "volume"])


def difference_metrics(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    key: str,
    left_suffix: str,
    right_suffix: str,
) -> dict[str, object]:
    columns = ["open", "high", "low", "close", "volume"]
    joined = left[[key, *columns]].merge(
        right[[key, *columns]],
        on=key,
        how="outer",
        suffixes=(left_suffix, right_suffix),
        indicator=True,
    )
    common = joined[joined["_merge"] == "both"].copy()
    metrics: dict[str, object] = {
        "left_rows": int(len(left)),
        "right_rows": int(len(right)),
        "common_rows": int(len(common)),
        "left_only_rows": int((joined["_merge"] == "left_only").sum()),
        "right_only_rows": int((joined["_merge"] == "right_only").sum()),
    }
    for column in columns:
        a = pd.to_numeric(common[f"{column}{left_suffix}"], errors="coerce")
        b = pd.to_numeric(common[f"{column}{right_suffix}"], errors="coerce")
        absolute = (a - b).abs()
        denominator = b.abs().replace(0, pd.NA)
        relative = absolute / denominator
        metrics[column] = {
            "mean_abs_diff": float(absolute.mean()) if absolute.notna().any() else None,
            "max_abs_diff": float(absolute.max()) if absolute.notna().any() else None,
            "mean_abs_relative_diff": float(relative.mean()) if relative.notna().any() else None,
            "exact_match_rate": float((absolute == 0).mean()) if absolute.notna().any() else None,
        }
    return metrics


def main() -> int:
    args = parse_args()
    python_stock_root = Path(args.python_stock_root)
    output_root = (ROOT / args.output_root).resolve()
    report_root = (ROOT / args.report_root).resolve()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = output_root / run_id
    raw_root = run_root / "raw"
    normalized_root = run_root / "normalized"
    comparison_root = run_root / "comparison"
    downloaded_at = utc_now()
    tickers = tuple(
        dict.fromkeys(part.strip().upper() for part in args.tickers.split(",") if part.strip())
    )
    dst_dates = tuple(part.strip() for part in args.dst_dates.split(",") if part.strip())
    if not tickers:
        raise RuntimeError("no tickers supplied")

    client = ResearchMarketDataClient(
        python_stock_root,
        timeout=args.timeout,
        max_retries=args.max_retries,
        request_spacing=args.request_spacing,
    )
    artifacts: list[dict[str, object]] = []
    coverage_by_ticker: dict[str, dict[str, dict[str, object]]] = {}
    daily_frames: dict[str, pd.DataFrame] = {}
    minute_frames: dict[str, pd.DataFrame] = {}
    yfinance_frames: dict[str, pd.DataFrame] = {}

    spec = requests.get(OPENAPI_URL, timeout=args.timeout).json()
    api_version = str(spec.get("info", {}).get("version", "unknown"))

    stock_master = client.get("/api/v1/stocks", {"symbols": ",".join(tickers)})
    stock_master_path = write_json(
        raw_root / "stock_master.json",
        {
            "source": TOSS_SOURCE,
            "source_role": SOURCE_ROLE,
            "downloaded_at_utc": downloaded_at,
            "query": {"symbols": list(tickers)},
            "response": stock_master,
        },
    )
    stock_rows = stock_master.get("result")
    stock_rows = stock_rows if isinstance(stock_rows, list) else []
    supported = {
        str(row.get("symbol")).upper(): row
        for row in stock_rows
        if isinstance(row, dict) and row.get("symbol")
    }
    artifacts.append(
        {
            "source": TOSS_SOURCE,
            "ticker": "MULTI",
            "market_date": None,
            "artifact": relative(stock_master_path, ROOT),
            "artifact_kind": "RAW_STOCK_MASTER",
            "coverage": f"{len(supported)}/{len(tickers)}",
            "row_count": len(stock_rows),
            "downloaded_at_utc": downloaded_at,
            "sha256": sha256(stock_master_path),
        }
    )

    calendar_payloads: dict[str, dict[str, Any]] = {}
    calendar_bounds: dict[str, dict[str, tuple[pd.Timestamp, pd.Timestamp]]] = {}
    for market_date in (args.market_date, *dst_dates):
        payload = client.get("/api/v1/market-calendar/US", {"date": market_date})
        calendar_payloads[market_date] = payload
        day = calendar_day(payload, market_date)
        calendar_bounds[market_date] = session_bounds(day)
        path = write_json(
            raw_root / "calendar" / f"{market_date}.json",
            {
                "source": TOSS_SOURCE,
                "source_role": SOURCE_ROLE,
                "downloaded_at_utc": downloaded_at,
                "market_date": market_date,
                "response": payload,
            },
        )
        artifacts.append(
            {
                "source": TOSS_SOURCE,
                "ticker": "US_MARKET_CALENDAR",
                "market_date": market_date,
                "artifact": relative(path, ROOT),
                "artifact_kind": "RAW_CALENDAR",
                "coverage": "calendar",
                "row_count": 1,
                "downloaded_at_utc": downloaded_at,
                "sha256": sha256(path),
            }
        )

    minute_bounds = calendar_bounds[args.market_date]
    overall_end = max(end for _, end in minute_bounds.values())
    daily_before = (overall_end - pd.Timedelta(minutes=1)).isoformat()
    for ticker in tickers:
        if ticker not in supported:
            continue
        daily_payload = client.get(
            "/api/v1/candles",
            {
                "symbol": ticker,
                "interval": "1d",
                "count": 200,
                "before": daily_before,
                "adjusted": "true",
            },
        )
        daily_rows = payload_rows(daily_payload)
        daily_raw_path = write_json(
            raw_root / "daily" / f"{ticker}.json",
            {
                "source": TOSS_SOURCE,
                "source_role": SOURCE_ROLE,
                "downloaded_at_utc": downloaded_at,
                "ticker": ticker,
                "interval": "1d",
                "request_adjusted": True,
                "response": daily_payload,
            },
        )
        daily_digest = sha256(daily_raw_path)
        daily_market_dates = [
            pd.Timestamp(row["timestamp"]).tz_convert(NY).date().isoformat()
            for row in daily_rows
        ]
        normalized_daily = normalize_toss_rows(
            daily_rows,
            ticker=ticker,
            market_date="MULTI",
            interval="1d",
            bounds=None,
            downloaded_at=downloaded_at,
            raw_path=relative(daily_raw_path, ROOT),
            raw_digest=daily_digest,
        )
        if not normalized_daily.empty:
            normalized_daily["market_date"] = (
                pd.to_datetime(normalized_daily["bar_start_utc"], utc=True)
                .dt.tz_convert(NY)
                .dt.date.astype(str)
            )
        daily_path = normalized_root / "daily" / f"{ticker}.csv"
        daily_path.parent.mkdir(parents=True, exist_ok=True)
        normalized_daily.to_csv(daily_path, index=False)
        daily_frames[ticker] = normalized_daily
        artifacts.extend(
            [
                {
                    "source": TOSS_SOURCE,
                    "ticker": ticker,
                    "market_date": "MULTI",
                    "artifact": relative(daily_raw_path, ROOT),
                    "artifact_kind": "RAW_DAILY",
                    "coverage": f"{min(daily_market_dates) if daily_market_dates else None}..{max(daily_market_dates) if daily_market_dates else None}",
                    "row_count": len(daily_rows),
                    "downloaded_at_utc": downloaded_at,
                    "sha256": daily_digest,
                },
                {
                    "source": TOSS_SOURCE,
                    "ticker": ticker,
                    "market_date": "MULTI",
                    "artifact": relative(daily_path, ROOT),
                    "artifact_kind": "NORMALIZED_DAILY",
                    "coverage": f"{normalized_daily['market_date'].min() if not normalized_daily.empty else None}..{normalized_daily['market_date'].max() if not normalized_daily.empty else None}",
                    "row_count": len(normalized_daily),
                    "downloaded_at_utc": downloaded_at,
                    "sha256": sha256(daily_path),
                },
            ]
        )

        pages, minute_rows = collect_minute_session(
            client,
            ticker=ticker,
            market_date=args.market_date,
            bounds=minute_bounds,
            page_size=args.page_size,
            max_pages=args.max_pages,
        )
        minute_raw_path = write_json(
            raw_root / "minute" / args.market_date / f"{ticker}.json",
            {
                "source": TOSS_SOURCE,
                "source_role": SOURCE_ROLE,
                "downloaded_at_utc": downloaded_at,
                "ticker": ticker,
                "market_date": args.market_date,
                "interval": "1m",
                "pagination": "nextBefore",
                "pages": pages,
            },
        )
        minute_digest = sha256(minute_raw_path)
        normalized_minute = normalize_toss_rows(
            minute_rows,
            ticker=ticker,
            market_date=args.market_date,
            interval="1m",
            bounds=minute_bounds,
            downloaded_at=downloaded_at,
            raw_path=relative(minute_raw_path, ROOT),
            raw_digest=minute_digest,
        )
        minute_path = normalized_root / "minute" / args.market_date / f"{ticker}.csv"
        minute_path.parent.mkdir(parents=True, exist_ok=True)
        normalized_minute.to_csv(minute_path, index=False)
        minute_frames[ticker] = normalized_minute
        coverage_by_ticker[ticker] = session_coverage(normalized_minute, minute_bounds)
        artifacts.extend(
            [
                {
                    "source": TOSS_SOURCE,
                    "ticker": ticker,
                    "market_date": args.market_date,
                    "artifact": relative(minute_raw_path, ROOT),
                    "artifact_kind": "RAW_MINUTE",
                    "coverage": coverage_by_ticker[ticker],
                    "row_count": len(minute_rows),
                    "downloaded_at_utc": downloaded_at,
                    "sha256": minute_digest,
                },
                {
                    "source": TOSS_SOURCE,
                    "ticker": ticker,
                    "market_date": args.market_date,
                    "artifact": relative(minute_path, ROOT),
                    "artifact_kind": "NORMALIZED_MINUTE",
                    "coverage": coverage_by_ticker[ticker],
                    "row_count": len(normalized_minute),
                    "downloaded_at_utc": downloaded_at,
                    "sha256": sha256(minute_path),
                },
            ]
        )

    dst_results: list[dict[str, object]] = []
    dst_ticker = "AAPL"
    for market_date in dst_dates:
        bounds = calendar_bounds[market_date]
        regular_start = bounds["REGULAR"][0]
        before = (regular_start + pd.Timedelta(minutes=5)).isoformat()
        payload = client.get(
            "/api/v1/candles",
            {
                "symbol": dst_ticker,
                "interval": "1m",
                "count": 5,
                "before": before,
                "adjusted": "false",
            },
        )
        rows = payload_rows(payload)
        path = write_json(
            raw_root / "dst_samples" / f"{dst_ticker}_{market_date}.json",
            {
                "source": TOSS_SOURCE,
                "source_role": SOURCE_ROLE,
                "downloaded_at_utc": downloaded_at,
                "ticker": dst_ticker,
                "market_date": market_date,
                "response": payload,
            },
        )
        timestamps = [
            (pd.Timestamp(row["timestamp"]).tz_convert(UTC) - pd.Timedelta(minutes=1)).tz_convert(NY)
            for row in rows
        ]
        dst_results.append(
            {
                "market_date": market_date,
                "regular_open_kst": regular_start.isoformat(),
                "regular_open_ny": regular_start.tz_convert(NY).isoformat(),
                "first_sample_ny": min(timestamps).isoformat() if timestamps else None,
                "last_sample_ny": max(timestamps).isoformat() if timestamps else None,
                "ny_utc_offsets": sorted(
                    {timestamp.strftime("%z") for timestamp in timestamps}
                ),
                "row_count": len(rows),
            }
        )
        artifacts.append(
            {
                "source": TOSS_SOURCE,
                "ticker": dst_ticker,
                "market_date": market_date,
                "artifact": relative(path, ROOT),
                "artifact_kind": "RAW_DST_MINUTE_SAMPLE",
                "coverage": "regular_open_first_5m",
                "row_count": len(rows),
                "downloaded_at_utc": downloaded_at,
                "sha256": sha256(path),
            }
        )

    daily_comparisons: dict[str, object] = {}
    minute_comparisons: dict[str, object] = {}
    minute_comparisons_by_session: dict[str, dict[str, object]] = {}
    timestamp_alignment_audit: dict[str, object] = {}
    for ticker in tickers:
        toss_daily = daily_frames.get(ticker, pd.DataFrame())
        canonical_path = ROOT / "data" / f"{ticker}.csv"
        if canonical_path.exists() and not toss_daily.empty:
            canonical = pd.read_csv(canonical_path)
            canonical = canonical.rename(
                columns={
                    "Date": "market_date",
                    "Open": "open",
                    "High": "high",
                    "Low": "low",
                    "Close": "close",
                    "Volume": "volume",
                }
            )
            canonical["market_date"] = canonical["market_date"].astype(str).str[:10]
            daily_comparisons[ticker] = difference_metrics(
                toss_daily,
                canonical,
                key="market_date",
                left_suffix="_toss",
                right_suffix="_canonical",
            )

        try:
            yf_raw = yf.download(
                ticker,
                start=args.market_date,
                end=(pd.Timestamp(args.market_date) + pd.Timedelta(days=1)).date().isoformat(),
                interval="1m",
                prepost=True,
                auto_adjust=False,
                progress=False,
                threads=False,
            )
            yf_flat = flatten_yfinance(yf_raw, ticker)
            yf_normalized = normalize_yfinance_minute(
                yf_flat, ticker, args.market_date, minute_bounds
            )
        except Exception:
            yf_normalized = pd.DataFrame()
        yfinance_frames[ticker] = yf_normalized
        yf_path = comparison_root / "yfinance_normalized" / args.market_date / f"{ticker}.csv"
        yf_path.parent.mkdir(parents=True, exist_ok=True)
        yf_normalized.to_csv(yf_path, index=False)
        artifacts.append(
            {
                "source": YFINANCE_SOURCE,
                "ticker": ticker,
                "market_date": args.market_date,
                "artifact": relative(yf_path, ROOT),
                "artifact_kind": "NORMALIZED_MINUTE_COMPARISON",
                "coverage": "pre_regular_after",
                "row_count": len(yf_normalized),
                "downloaded_at_utc": downloaded_at,
                "sha256": sha256(yf_path),
            }
        )
        toss_minute = minute_frames.get(ticker, pd.DataFrame())
        if not toss_minute.empty and not yf_normalized.empty:
            toss_comparable = toss_minute[
                toss_minute["session"].isin(["PREMARKET", "REGULAR", "AFTER_HOURS"])
            ].copy()
            minute_comparisons[ticker] = difference_metrics(
                toss_comparable,
                yf_normalized,
                key="bar_start_utc",
                left_suffix="_toss",
                right_suffix="_canonical",
            )
            minute_comparisons_by_session[ticker] = {}
            for session_name in ("PREMARKET", "REGULAR", "AFTER_HOURS"):
                toss_session = toss_comparable[
                    toss_comparable["session"] == session_name
                ].copy()
                canonical_session = yf_normalized[
                    yf_normalized["session"] == session_name
                ].copy()
                minute_comparisons_by_session[ticker][session_name] = difference_metrics(
                    toss_session,
                    canonical_session,
                    key="bar_start_utc",
                    left_suffix="_toss",
                    right_suffix="_canonical",
                )
            documented_label = toss_comparable[
                toss_comparable["session"] == "REGULAR"
            ].copy()
            documented_label["source_timestamp_utc"] = pd.to_datetime(
                documented_label["source_timestamp"], utc=True
            ).map(lambda value: value.isoformat())
            canonical_label = yf_normalized[
                yf_normalized["session"] == "REGULAR"
            ].copy()
            canonical_label["source_timestamp_utc"] = canonical_label["bar_start_utc"]
            documented_metrics = difference_metrics(
                documented_label,
                canonical_label,
                key="source_timestamp_utc",
                left_suffix="_toss",
                right_suffix="_canonical",
            )
            normalized_metrics = minute_comparisons_by_session[ticker]["REGULAR"]
            timestamp_alignment_audit[ticker] = {
                "documented_as_bar_start_close_mean_abs_relative_diff": documented_metrics[
                    "close"
                ]["mean_abs_relative_diff"],
                "empirical_minus_1m_close_mean_abs_relative_diff": normalized_metrics[
                    "close"
                ]["mean_abs_relative_diff"],
                "documented_common_rows": documented_metrics["common_rows"],
                "empirical_minus_1m_common_rows": normalized_metrics["common_rows"],
                "selected_shift_minutes": -1,
                "selection_basis": (
                    "repeated lower close-price error versus yfinance on identical "
                    "US historical session"
                ),
            }

    unique_capability = {
        ticker: {
            "day_market_bars": int(
                (frame["session"] == "DAY_MARKET").sum()
            )
            if not frame.empty
            else 0,
            "premarket_bars": int((frame["session"] == "PREMARKET").sum())
            if not frame.empty
            else 0,
            "regular_bars": int((frame["session"] == "REGULAR").sum())
            if not frame.empty
            else 0,
            "after_hours_bars": int((frame["session"] == "AFTER_HOURS").sum())
            if not frame.empty
            else 0,
            "trade_value_available": False,
        }
        for ticker, frame in minute_frames.items()
    }

    summary = {
        "status": "EXPERIMENTAL",
        "scope": "Toss US market-data capability audit; no performance experiment",
        "source_role": SOURCE_ROLE,
        "runtime_enabled": False,
        "champion_connected": False,
        "production_scoring_connected": False,
        "paper_or_live_execution_changed": False,
        "training_run": False,
        "order_endpoints_called": False,
        "downloaded_at_utc": downloaded_at,
        "openapi": {"url": OPENAPI_URL, "version": api_version},
        "authorized_existing_integration": True,
        "credentials_persisted_in_artifacts": False,
        "market_date": args.market_date,
        "tickers_requested": list(tickers),
        "tickers_supported": sorted(supported),
        "stock_master": {
            ticker: {
                "market": supported[ticker].get("market"),
                "currency": supported[ticker].get("currency"),
                "securityType": supported[ticker].get("securityType"),
                "status": supported[ticker].get("status"),
            }
            for ticker in sorted(supported)
        },
        "timestamp": {
            "documented_api_semantics": "BAR_START",
            "observed_minute_semantics": "BAR_END_LABEL_EMPIRICAL",
            "documented_observed_conflict": True,
            "normalization_rule": "1m bar_start_utc = source timestamp - 1 minute",
            "raw_timezone": "ISO8601 offset supplied by Toss; observed Asia/Seoul offset",
            "normalized_timezone": UTC,
            "comparison_timezone": NY,
            "feature_availability_rule": "1m bar usable no earlier than raw source timestamp (normalized bar end)",
            "dst_samples": dst_results,
            "alignment_evidence": timestamp_alignment_audit,
        },
        "pagination": {
            "method": "pass response nextBefore unchanged",
            "max_pages": args.max_pages,
            "page_size": args.page_size,
        },
        "rate_limit": {
            "requests": client.request_count,
            "http_429_retries": client.rate_limit_retries,
            "retry_after_seconds": client.retry_after_seconds,
            "handling": "Retry-After or X-RateLimit-Reset, bounded retries",
        },
        "session_coverage": coverage_by_ticker,
        "additional_information": unique_capability,
        "daily_cross_check": daily_comparisons,
        "daily_comparison_adjustment": {
            "toss_adjusted": True,
            "phoenix_canonical_yfinance_auto_adjust": True,
        },
        "minute_cross_check": minute_comparisons,
        "minute_cross_check_by_session": minute_comparisons_by_session,
        "source_mixing_prohibited": True,
        "artifacts": artifacts,
    }
    manifest_path = write_json(run_root / "manifest.json", summary)
    summary["manifest_sha256"] = sha256(manifest_path)
    summary["manifest_path"] = relative(manifest_path, ROOT)
    report_path = write_json(report_root / f"capability_audit_{run_id}.json", summary)
    latest_path = write_json(report_root / "capability_audit_latest.json", summary)
    print(
        json.dumps(
            {
                "status": summary["status"],
                "run_root": relative(run_root, ROOT),
                "report": relative(report_path, ROOT),
                "latest": relative(latest_path, ROOT),
                "tickers_supported": len(supported),
                "requests": client.request_count,
                "http_429_retries": client.rate_limit_retries,
                "credentials_persisted": False,
                "order_endpoints_called": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
