#!/usr/bin/env python3
"""Validate Toss US one-minute timestamp semantics without touching runtime paths."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

from phoenix_toss_us_capability_audit import (
    KST,
    NY,
    ROOT,
    UTC,
    ResearchMarketDataClient,
    calendar_day,
    classify_session,
    payload_next_before,
    payload_rows,
    session_bounds,
    sha256,
    write_json,
)


SYMBOLS = ("AAPL", "MSFT", "NVDA", "AMD", "AVGO", "TSM", "QQQ", "SMH", "SOXX")
DATE_SPECS = (
    ("2025-09-30", "FALL_Q3_END", "1h"),
    ("2025-11-28", "EARLY_CLOSE", "1h"),
    ("2026-01-02", "YEAR_START", "1h"),
    ("2026-03-06", "PRE_DST", "1h"),
    ("2026-03-13", "POST_DST", "1h"),
    ("2026-03-31", "Q1_END", "1h"),
    ("2026-06-30", "SUMMER_Q2_END", "1m"),
    ("2026-07-10", "SUMMER", "1m"),
    ("2026-07-17", "SUMMER", "1m"),
)
SESSIONS = ("PREMARKET", "REGULAR", "AFTER_HOURS")
SOURCE = "TOSS_US"
ROLE = "SECONDARY_RESEARCH_SOURCE"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-stock-root", default="/home/sysadmin/python-stock")
    parser.add_argument("--output-root", default="data/research/toss_us/timestamp_contract_v1")
    parser.add_argument("--report-root", default="reports/research/toss_us")
    parser.add_argument("--request-spacing", type=float, default=0.25)
    parser.add_argument("--max-retries", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--page-size", type=int, default=200)
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve()))


def collect_extended(
    client: ResearchMarketDataClient,
    *,
    symbol: str,
    bounds: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
    page_size: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    start = bounds["PREMARKET"][0]
    end = bounds["AFTER_HOURS"][1]
    cursor = end.isoformat()
    pages: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    max_pages = math.ceil(int((end - start).total_seconds() // 60) / page_size) + 2
    for page_number in range(1, max_pages + 1):
        payload = client.get(
            "/api/v1/candles",
            {
                "symbol": symbol,
                "interval": "1m",
                "count": page_size,
                "before": cursor,
                "adjusted": "false",
            },
        )
        page_rows = payload_rows(payload)
        next_before = payload_next_before(payload)
        pages.append(
            {
                "page": page_number,
                "before": cursor,
                "nextBefore": next_before,
                "row_count": len(page_rows),
                "response": payload,
            }
        )
        if not page_rows:
            break
        rows.extend(page_rows)
        stamps = pd.to_datetime(
            pd.Series([row.get("timestamp") for row in page_rows]),
            errors="coerce",
            utc=True,
        ).dropna()
        if stamps.empty or stamps.min() <= start.tz_convert(UTC):
            break
        if not next_before or next_before == cursor:
            break
        cursor = next_before
    kept: dict[str, dict[str, Any]] = {}
    for row in rows:
        stamp = pd.Timestamp(row.get("timestamp"))
        if stamp.tzinfo is None:
            continue
        local = stamp.tz_convert(KST)
        if start < local <= end:
            kept[stamp.isoformat()] = row
    return pages, list(kept.values())


def normalize_toss(
    rows: list[dict[str, Any]],
    bounds: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
) -> pd.DataFrame:
    output: list[dict[str, Any]] = []
    for row in rows:
        raw = pd.Timestamp(row.get("timestamp"))
        if raw.tzinfo is None:
            continue
        raw_utc = raw.tz_convert(UTC)
        shifted = raw_utc - pd.Timedelta(minutes=1)
        output.append(
            {
                "raw_timestamp_utc": raw_utc,
                "bar_start_utc": shifted,
                "bar_end_utc": raw_utc,
                "bar_available_at_utc": raw_utc,
                "bar_start_et": shifted.tz_convert(NY),
                "bar_end_et": raw_utc.tz_convert(NY),
                "session": classify_session(shifted.tz_convert(KST), bounds),
                "open": pd.to_numeric(row.get("openPrice"), errors="coerce"),
                "high": pd.to_numeric(row.get("highPrice"), errors="coerce"),
                "low": pd.to_numeric(row.get("lowPrice"), errors="coerce"),
                "close": pd.to_numeric(row.get("closePrice"), errors="coerce"),
                "volume": pd.to_numeric(row.get("volume"), errors="coerce"),
            }
        )
    frame = pd.DataFrame(output)
    if frame.empty:
        return frame
    return (
        frame.dropna(subset=["open", "high", "low", "close", "volume"])
        .sort_values("raw_timestamp_utc")
        .drop_duplicates("raw_timestamp_utc", keep="last")
    )


def flatten_yfinance(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    output = frame.copy()
    if isinstance(output.columns, pd.MultiIndex):
        if symbol in output.columns.get_level_values(-1):
            output = output.xs(symbol, axis=1, level=-1)
        else:
            output.columns = output.columns.get_level_values(0)
    output = output.rename(columns={str(c): str(c).lower() for c in output.columns})
    output.index = pd.to_datetime(output.index)
    if output.index.tz is None:
        output.index = output.index.tz_localize(NY)
    return output


def canonical_frame(symbol: str, market_date: str, interval: str) -> pd.DataFrame:
    raw = yf.download(
        symbol,
        start=market_date,
        end=(pd.Timestamp(market_date) + pd.Timedelta(days=1)).date().isoformat(),
        interval=interval,
        prepost=True,
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    flat = flatten_yfinance(raw, symbol)
    if flat.empty:
        return pd.DataFrame()
    out = flat.reset_index()
    stamp_col = out.columns[0]
    out = out.rename(columns={stamp_col: "timestamp"})
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
    return out[["timestamp", "open", "high", "low", "close", "volume"]].dropna(
        subset=["timestamp", "open", "high", "low", "close"]
    )


def aggregate_toss_hourly(
    frame: pd.DataFrame,
    *,
    timestamp_column: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    regular = frame.copy()
    stamps = pd.to_datetime(regular[timestamp_column], utc=True)
    start_utc, end_utc = start.tz_convert(UTC), end.tz_convert(UTC)
    regular = regular[(stamps >= start_utc) & (stamps < end_utc)].copy()
    if regular.empty:
        return pd.DataFrame()
    stamps = pd.to_datetime(regular[timestamp_column], utc=True)
    elapsed = ((stamps - start_utc).dt.total_seconds() // 3600).astype(int)
    regular["timestamp"] = elapsed.map(lambda value: start_utc + pd.Timedelta(hours=int(value)))
    return (
        regular.groupby("timestamp", as_index=False)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            source_minute_count=("close", "size"),
        )
        .sort_values("timestamp")
    )


def filter_canonical_session(
    frame: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    if frame.empty:
        return frame
    stamps = pd.to_datetime(frame["timestamp"], utc=True)
    return frame[
        (stamps >= start.tz_convert(UTC)) & (stamps < end.tz_convert(UTC))
    ].copy()


def error_metrics(left: pd.DataFrame, right: pd.DataFrame) -> dict[str, Any]:
    if left.empty or right.empty:
        return {"common_rows": 0, "ohlc_mard": None, "close_mard": None}
    joined = left.merge(right, on="timestamp", suffixes=("_toss", "_canonical"))
    relative_values: list[pd.Series] = []
    close_relative: pd.Series | None = None
    for column in ("open", "high", "low", "close"):
        a = pd.to_numeric(joined[f"{column}_toss"], errors="coerce")
        b = pd.to_numeric(joined[f"{column}_canonical"], errors="coerce")
        relative = (a - b).abs() / b.abs().replace(0, pd.NA)
        relative_values.append(relative)
        if column == "close":
            close_relative = relative
    stacked = pd.concat(relative_values, ignore_index=True)
    return {
        "common_rows": int(len(joined)),
        "ohlc_mard": float(stacked.mean()) if stacked.notna().any() else None,
        "close_mard": (
            float(close_relative.mean())
            if close_relative is not None and close_relative.notna().any()
            else None
        ),
    }


def minute_comparison(
    toss: pd.DataFrame,
    canonical: pd.DataFrame,
    *,
    session: str,
    bounds: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    start, end = bounds[session]
    canonical_session = filter_canonical_session(canonical, start, end)
    toss_session = toss[toss["session"] == session].copy()
    shifted = toss_session.rename(columns={"bar_start_utc": "timestamp"})
    same = toss_session.rename(columns={"raw_timestamp_utc": "timestamp"})
    return error_metrics(same, canonical_session), error_metrics(shifted, canonical_session)


def session_boundary(
    frame: pd.DataFrame,
    bounds: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
    session: str,
) -> dict[str, Any]:
    subset = frame[frame["session"] == session].copy()
    start, end = bounds[session]
    expected = int((end - start).total_seconds() // 60)
    if subset.empty:
        return {
            "expected_bars": expected,
            "observed_bars": 0,
            "first_bar_start_et": None,
            "last_bar_start_et": None,
            "first_raw_timestamp_et": None,
            "last_raw_timestamp_et": None,
        }
    return {
        "expected_bars": expected,
        "observed_bars": int(len(subset)),
        "first_bar_start_et": subset["bar_start_et"].min().isoformat(),
        "last_bar_start_et": subset["bar_start_et"].max().isoformat(),
        "first_raw_timestamp_et": subset["raw_timestamp_utc"].min().tz_convert(NY).isoformat(),
        "last_raw_timestamp_et": subset["raw_timestamp_utc"].max().tz_convert(NY).isoformat(),
    }


def main() -> int:
    args = parse_args()
    output_root = (ROOT / args.output_root).resolve()
    report_root = (ROOT / args.report_root).resolve()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = output_root / run_id
    raw_root = run_root / "raw"
    normalized_root = run_root / "normalized"
    canonical_root = run_root / "canonical"
    downloaded_at = now_utc()
    client = ResearchMarketDataClient(
        Path(args.python_stock_root),
        timeout=args.timeout,
        max_retries=args.max_retries,
        request_spacing=args.request_spacing,
    )
    artifacts: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    boundaries: list[dict[str, Any]] = []
    calendar_records: dict[str, Any] = {}

    for market_date, season, canonical_interval in DATE_SPECS:
        calendar_payload = client.get("/api/v1/market-calendar/US", {"date": market_date})
        day = calendar_day(calendar_payload, market_date)
        bounds = session_bounds(day)
        calendar_records[market_date] = {
            "season": season,
            "canonical_interval": canonical_interval,
            "sessions": {
                key: {"start": value[0].isoformat(), "end": value[1].isoformat()}
                for key, value in bounds.items()
            },
        }
        calendar_path = write_json(
            raw_root / "calendar" / f"{market_date}.json",
            {
                "source": SOURCE,
                "source_role": ROLE,
                "downloaded_at_utc": downloaded_at,
                "market_date": market_date,
                "response": calendar_payload,
            },
        )
        artifacts.append(
            {
                "source": SOURCE,
                "ticker": "US_MARKET_CALENDAR",
                "market_date": market_date,
                "session": "ALL",
                "coverage": "calendar",
                "row_count": 1,
                "first_timestamp": None,
                "last_timestamp": None,
                "artifact": rel(calendar_path),
                "sha256": sha256(calendar_path),
            }
        )

        for symbol in SYMBOLS:
            pages, raw_rows = collect_extended(
                client,
                symbol=symbol,
                bounds=bounds,
                page_size=args.page_size,
            )
            raw_path = write_json(
                raw_root / "minute" / market_date / f"{symbol}.json",
                {
                    "source": SOURCE,
                    "source_role": ROLE,
                    "downloaded_at_utc": downloaded_at,
                    "ticker": symbol,
                    "market_date": market_date,
                    "timestamp_documented_semantics": "BAR_START",
                    "pages": pages,
                },
            )
            frame = normalize_toss(raw_rows, bounds)
            normalized_path = normalized_root / market_date / f"{symbol}.csv"
            normalized_path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(normalized_path, index=False)
            for session in SESSIONS:
                boundary = session_boundary(frame, bounds, session)
                boundaries.append(
                    {
                        "ticker": symbol,
                        "market_date": market_date,
                        "season": season,
                        "session": session,
                        **boundary,
                    }
                )
            first = frame["raw_timestamp_utc"].min().isoformat() if not frame.empty else None
            last = frame["raw_timestamp_utc"].max().isoformat() if not frame.empty else None
            coverage = {
                session: session_boundary(frame, bounds, session)
                for session in SESSIONS
            }
            for path, kind in ((raw_path, "RAW"), (normalized_path, "NORMALIZED")):
                artifacts.append(
                    {
                        "source": SOURCE,
                        "ticker": symbol,
                        "market_date": market_date,
                        "session": "PREMARKET_REGULAR_AFTER_HOURS",
                        "coverage": coverage,
                        "row_count": int(len(frame)),
                        "first_timestamp": first,
                        "last_timestamp": last,
                        "artifact_kind": kind,
                        "artifact": rel(path),
                        "sha256": sha256(path),
                    }
                )

            canonical = canonical_frame(symbol, market_date, canonical_interval)
            canonical_path = canonical_root / canonical_interval / market_date / f"{symbol}.csv"
            canonical_path.parent.mkdir(parents=True, exist_ok=True)
            canonical.to_csv(canonical_path, index=False)
            artifacts.append(
                {
                    "source": "YFINANCE_CANONICAL_COMPARISON",
                    "ticker": symbol,
                    "market_date": market_date,
                    "session": "PREMARKET_REGULAR_AFTER_HOURS",
                    "coverage": canonical_interval,
                    "row_count": int(len(canonical)),
                    "first_timestamp": (
                        canonical["timestamp"].min().isoformat() if not canonical.empty else None
                    ),
                    "last_timestamp": (
                        canonical["timestamp"].max().isoformat() if not canonical.empty else None
                    ),
                    "artifact_kind": "CANONICAL_COMPARISON",
                    "artifact": rel(canonical_path),
                    "sha256": sha256(canonical_path),
                }
            )

            if canonical_interval == "1m":
                for session in SESSIONS:
                    same, shifted = minute_comparison(
                        frame, canonical, session=session, bounds=bounds
                    )
                    comparisons.append(
                        {
                            "ticker": symbol,
                            "market_date": market_date,
                            "season": season,
                            "canonical_interval": "1m",
                            "session": session,
                            "same": same,
                            "minus_1m": shifted,
                        }
                    )
            else:
                start, end = bounds["REGULAR"]
                canonical_regular = filter_canonical_session(canonical, start, end)
                same_hourly = aggregate_toss_hourly(
                    frame,
                    timestamp_column="raw_timestamp_utc",
                    start=start,
                    end=end,
                )
                shifted_hourly = aggregate_toss_hourly(
                    frame,
                    timestamp_column="bar_start_utc",
                    start=start,
                    end=end,
                )
                comparisons.append(
                    {
                        "ticker": symbol,
                        "market_date": market_date,
                        "season": season,
                        "canonical_interval": "1h",
                        "session": "REGULAR",
                        "same": error_metrics(same_hourly, canonical_regular),
                        "minus_1m": error_metrics(shifted_hourly, canonical_regular),
                    }
                )

    comparison_frame = pd.json_normalize(comparisons, sep="_")
    comparison_path = run_root / "timestamp_alignment_comparisons.csv"
    comparison_frame.to_csv(comparison_path, index=False)
    boundary_frame = pd.DataFrame(boundaries)
    boundary_path = run_root / "session_boundaries.csv"
    boundary_frame.to_csv(boundary_path, index=False)
    for path, kind, rows in (
        (comparison_path, "ALIGNMENT_COMPARISON", len(comparison_frame)),
        (boundary_path, "SESSION_BOUNDARY", len(boundary_frame)),
    ):
        artifacts.append(
            {
                "source": "DERIVED_AUDIT",
                "ticker": "MULTI",
                "market_date": "MULTI",
                "session": "MULTI",
                "coverage": kind,
                "row_count": int(rows),
                "first_timestamp": None,
                "last_timestamp": None,
                "artifact_kind": kind,
                "artifact": rel(path),
                "sha256": sha256(path),
            }
        )

    evaluable_1m = comparison_frame[
        (comparison_frame["canonical_interval"] == "1m")
        & (comparison_frame["same_ohlc_mard"].notna())
        & (comparison_frame["minus_1m_ohlc_mard"].notna())
    ].copy()
    evaluable_regular_1m = evaluable_1m[evaluable_1m["session"] == "REGULAR"].copy()
    evaluable_1h = comparison_frame[
        (comparison_frame["canonical_interval"] == "1h")
        & (comparison_frame["same_ohlc_mard"].notna())
        & (comparison_frame["minus_1m_ohlc_mard"].notna())
    ].copy()
    for frame in (evaluable_1m, evaluable_regular_1m, evaluable_1h):
        frame["improved"] = frame["minus_1m_ohlc_mard"] < frame["same_ohlc_mard"]
        frame["error_ratio"] = (
            frame["minus_1m_ohlc_mard"]
            / frame["same_ohlc_mard"].replace(0, pd.NA)
        )

    regular_boundary = boundary_frame[boundary_frame["session"] == "REGULAR"].copy()
    regular_boundary["expected_first"] = regular_boundary["market_date"].map(
        lambda value: pd.Timestamp(f"{value} 09:30", tz=NY).isoformat()
    )
    regular_boundary["first_ok"] = (
        regular_boundary["first_bar_start_et"].str[:16]
        == regular_boundary["expected_first"].str[:16]
    )
    regular_boundary["last_ok"] = regular_boundary.apply(
        lambda row: (
            pd.Timestamp(row["last_bar_start_et"]).tz_convert(UTC)
            + pd.Timedelta(minutes=1)
        )
        == pd.Timestamp(
            calendar_records[row["market_date"]]["sessions"]["REGULAR"]["end"]
        ).tz_convert(UTC),
        axis=1,
    )

    gate_metrics = {
        "regular_1m_pairs": int(len(evaluable_regular_1m)),
        "regular_1m_improvement_rate": (
            float(evaluable_regular_1m["improved"].mean())
            if not evaluable_regular_1m.empty
            else None
        ),
        "regular_1m_median_error_ratio": (
            float(evaluable_regular_1m["error_ratio"].median())
            if not evaluable_regular_1m.empty
            else None
        ),
        "all_1m_session_pairs": int(len(evaluable_1m)),
        "all_1m_session_improvement_rate": (
            float(evaluable_1m["improved"].mean()) if not evaluable_1m.empty else None
        ),
        "seasonal_1h_pairs": int(len(evaluable_1h)),
        "seasonal_1h_improvement_rate": (
            float(evaluable_1h["improved"].mean()) if not evaluable_1h.empty else None
        ),
        "seasonal_1h_median_error_ratio": (
            float(evaluable_1h["error_ratio"].median()) if not evaluable_1h.empty else None
        ),
        "regular_first_boundary_pass_rate": float(regular_boundary["first_ok"].mean()),
        "regular_last_boundary_pass_rate": float(regular_boundary["last_ok"].mean()),
        "canonical_1m_season_categories": sorted(
            evaluable_regular_1m["season"].unique().tolist()
        ),
        "canonical_1m_dates": sorted(
            evaluable_regular_1m["market_date"].unique().tolist()
        ),
    }
    thresholds = {
        "regular_1m_improvement_rate_min": 0.90,
        "regular_1m_median_error_ratio_max": 0.25,
        "all_1m_session_improvement_rate_min": 0.75,
        "seasonal_1h_improvement_rate_min": 0.80,
        "seasonal_1h_median_error_ratio_max": 0.80,
        "regular_boundary_pass_rate_min": 1.0,
        "required_canonical_1m_categories": [
            "YEAR_START",
            "PRE_DST",
            "POST_DST",
            "Q1_END_OR_Q3_END",
            "SUMMER",
            "EARLY_CLOSE",
        ],
    }
    quantitative_pass = bool(
        gate_metrics["regular_1m_pairs"]
        and gate_metrics["regular_1m_improvement_rate"]
        >= thresholds["regular_1m_improvement_rate_min"]
        and gate_metrics["regular_1m_median_error_ratio"]
        <= thresholds["regular_1m_median_error_ratio_max"]
        and gate_metrics["all_1m_session_improvement_rate"]
        >= thresholds["all_1m_session_improvement_rate_min"]
        and gate_metrics["seasonal_1h_improvement_rate"]
        >= thresholds["seasonal_1h_improvement_rate_min"]
        and gate_metrics["seasonal_1h_median_error_ratio"]
        <= thresholds["seasonal_1h_median_error_ratio_max"]
        and gate_metrics["regular_first_boundary_pass_rate"]
        >= thresholds["regular_boundary_pass_rate_min"]
        and gate_metrics["regular_last_boundary_pass_rate"]
        >= thresholds["regular_boundary_pass_rate_min"]
    )
    canonical_1m_categories = set(gate_metrics["canonical_1m_season_categories"])
    required_categories_met = bool(
        {"YEAR_START", "PRE_DST", "POST_DST", "EARLY_CLOSE"}.issubset(
            canonical_1m_categories
        )
        and "SUMMER" in canonical_1m_categories
        and {"FALL_Q3_END", "Q1_END"}.intersection(canonical_1m_categories)
    )
    status = "TOSS_TIMESTAMP_CONTRACT_INCONCLUSIVE"
    reasons = []
    if not quantitative_pass:
        reasons.append("one or more frozen quantitative alignment gates failed")
    if not required_categories_met:
        reasons.append(
            "independent canonical 1-minute data is unavailable for year-start, "
            "DST, fall/quarter-end, and early-close categories"
        )
    report = {
        "status": status,
        "source_role": ROLE,
        "generated_at_utc": now_utc(),
        "run_id": run_id,
        "symbols": list(SYMBOLS),
        "date_specs": [
            {"market_date": d, "category": category, "canonical_interval": interval}
            for d, category, interval in DATE_SPECS
        ],
        "timestamp_contract_candidate": {
            "bar_start_utc": "toss_timestamp - 1 minute",
            "bar_end_utc": "toss_timestamp",
            "bar_available_at_utc": "toss_timestamp",
            "execution_rule": "execution timestamp must be >= bar_available_at_utc",
        },
        "gate_thresholds": thresholds,
        "gate_metrics": gate_metrics,
        "quantitative_pass": quantitative_pass,
        "required_canonical_1m_categories_met": required_categories_met,
        "reasons": reasons,
        "calendar": calendar_records,
        "comparisons": comparisons,
        "artifacts": artifacts,
        "requests": client.request_count,
        "http_429_retries": client.rate_limit_retries,
        "credentials_persisted": False,
        "order_or_account_endpoints_called": False,
        "production_connected": False,
    }
    manifest_path = write_json(run_root / "manifest.json", report)
    report["dataset_sha256"] = sha256(manifest_path)
    report["manifest_path"] = rel(manifest_path)
    json_path = write_json(report_root / "toss_us_timestamp_contract_report.json", report)

    lines = [
        "# Toss US timestamp contract report",
        "",
        f"- Status: `{status}`",
        f"- Run: `{run_id}`",
        f"- Dataset SHA256: `{report['dataset_sha256']}`",
        f"- Symbols: {len(SYMBOLS)}",
        f"- Dates: {len(DATE_SPECS)}",
        f"- Toss requests: {client.request_count}",
        f"- HTTP 429 retries: {client.rate_limit_retries}",
        "",
        "## Candidate contract",
        "",
        "```text",
        "bar_start_utc = toss_timestamp - 1 minute",
        "bar_end_utc = toss_timestamp",
        "bar_available_at_utc = toss_timestamp",
        "execution >= bar_available_at_utc",
        "```",
        "",
        "## Gate metrics",
        "",
    ]
    for key, value in gate_metrics.items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(
        [
            "",
            "## Closure",
            "",
            *[f"- {reason}" for reason in reasons],
            "",
            "The observed convention remains research-only. Extended-hours dataset",
            "construction and Premarket V1 performance research are blocked by the",
            "timestamp gate. Champion and runtime paths remain unchanged.",
            "",
        ]
    )
    markdown_path = report_root / "toss_us_timestamp_contract_report.md"
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(lines), encoding="utf-8")

    closure_common = {
        "blocked_by": status,
        "timestamp_report": rel(json_path),
        "timestamp_report_sha256": sha256(json_path),
        "dataset_sha256": report["dataset_sha256"],
        "champion": "20260714_000251",
        "champion_changed": False,
        "production_changed": False,
        "paper_live_changed": False,
        "training_run": False,
        "order_or_account_endpoints_called": False,
    }
    write_json(
        report_root / "toss_us_extended_hours_capability.json",
        {"status": "BLOCKED_BY_TIMESTAMP_CONTRACT", **closure_common},
    )
    write_json(
        report_root / "toss_us_extended_hours_manifest.json",
        {
            "status": "NOT_BUILT",
            "reason": "Phase 1 requires a passed timestamp contract",
            **closure_common,
        },
    )
    write_json(
        report_root / "toss_us_premarket_preregistration.json",
        {
            "status": "NOT_PREREGISTERED",
            "reason": "Phase 2 requires a passed extended-hours dataset gate",
            **closure_common,
        },
    )
    signal_path = report_root / "toss_us_premarket_signal_report.md"
    signal_path.write_text(
        "# Toss US Premarket V1 signal report\n\n"
        "- Status: `NOT_RUN`\n"
        f"- Blocked by: `{status}`\n"
        "- No feature selection, residual test, alpha test, or Challenger was run.\n",
        encoding="utf-8",
    )
    write_json(
        report_root / "toss_us_premarket_closure.json",
        {
            "status": "TOSS_US_PREMARKET_CONTEXT_NOT_STARTED",
            "reason": "timestamp contract gate did not pass",
            **closure_common,
        },
    )
    print(
        json.dumps(
            {
                "status": status,
                "run_id": run_id,
                "report": rel(markdown_path),
                "dataset_sha256": report["dataset_sha256"],
                "requests": client.request_count,
                "429_retries": client.rate_limit_retries,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
