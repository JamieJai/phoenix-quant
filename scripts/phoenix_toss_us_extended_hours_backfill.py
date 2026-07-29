#!/usr/bin/env python3
"""Resumable research-only Toss US extended-hours backfill.

The historical universe is the current Phoenix universe backcast and is
therefore exploratory only. This script never calls order/account endpoints.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from phoenix_core.config import load_config
from phoenix_toss_us_capability_audit import (
    ResearchMarketDataClient,
    calendar_day,
    session_bounds,
    sha256,
    write_json,
)
from phoenix_toss_us_timestamp_contract import collect_extended, normalize_toss


SOURCE = "TOSS_US"
DATASET = "TOSS_US_EXTENDED_HOURS_DATASET_V1_RESEARCH"
SESSIONS = ("PREMARKET", "REGULAR", "AFTER_HOURS")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--daily-cache", default="data/SPY.csv")
    parser.add_argument(
        "--output-root",
        default="data/research/toss_us/extended_hours_dataset_v1_research/governed_v1",
    )
    parser.add_argument("--python-stock-root", default="/home/sysadmin/python-stock")
    parser.add_argument("--target-sessions", type=int, default=252)
    parser.add_argument("--minimum-sessions", type=int, default=180)
    parser.add_argument("--max-ticker-dates", type=int, default=10)
    parser.add_argument("--request-spacing", type=float, default=0.20)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--max-retries", type=int, default=8)
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument(
        "--source-capabilities",
        default=(
            "research/source_capabilities/"
            "TOSS_US_EXTENDED_HOURS_DATASET_V1_RESEARCH.json"
        ),
        help=(
            "Audited research-only source capability contract. Symbols marked "
            "terminal_unavailable are finalized as missing without imputation."
        ),
    )
    parser.add_argument(
        "--finalize-terminal-capabilities-only",
        action="store_true",
        help=(
            "Write only terminal source-unavailable manifests for every planned "
            "date, make zero market-data requests, then exit."
        ),
    )
    return parser.parse_args()


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def artifact_label(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_dates(path: Path, count: int) -> list[str]:
    frame = pd.read_csv(path)
    column = "Date" if "Date" in frame.columns else frame.columns[0]
    current_ny_date = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    values = sorted(
        value
        for value in set(frame[column].astype(str).str[:10])
        if value < current_ny_date
    )
    if len(values) < count:
        raise RuntimeError(f"daily calendar cache has only {len(values)} dates, need {count}")
    return values[-count:]


def initialize_plan(
    *,
    plan_path: Path,
    config_path: Path,
    daily_cache: Path,
    target_sessions: int,
    minimum_sessions: int,
) -> dict:
    config = load_config(str(config_path))
    tickers = list(
        dict.fromkeys(
            [*config.universe, *config.market_etfs, "SPY", "QQQ", "SMH", "SOXX"]
        )
    )
    dates = load_dates(daily_cache, target_sessions)
    config_sha = sha256(config_path)
    plan = {
        "dataset": DATASET,
        "created_at_utc": utc_now(),
        "scope": "HISTORICAL_EXPLORATORY_ONLY",
        "evidence_level": "LEVEL_C_CURRENT_UNIVERSE_BACKCAST",
        "flags": [
            "SURVIVORSHIP_BIAS_POSSIBLE",
            "CONFIRMATORY_OOS_FORBIDDEN",
            "PROMOTION_EVIDENCE_FORBIDDEN",
        ],
        "source": SOURCE,
        "source_role": "SECONDARY_RESEARCH_SOURCE",
        "timestamp_convention": "TOSS_TIMESTAMP_RESEARCH_PROVISIONAL_V1",
        "config_path": str(config_path.relative_to(ROOT)),
        "config_sha256": config_sha,
        "daily_calendar_source": str(daily_cache.relative_to(ROOT)),
        "ticker_count": len(tickers),
        "tickers": tickers,
        "session_count": len(dates),
        "market_dates": dates,
        "target_sessions": target_sessions,
        "minimum_sessions": minimum_sessions,
        "total_ticker_dates": len(tickers) * len(dates),
        "source_isolation": {
            "price": "TOSS_US_ONLY",
            "volume": "TOSS_US_ONLY",
            "yfinance_role": "CALENDAR_AND_VALIDATION_ONLY",
            "mixed_trade_path_forbidden": True,
        },
        "missingness": {
            "fill_missing_bars": False,
            "zero_return_imputation": False,
            "zero_volume_imputation": False,
            "source_missing_rule": "regular coverage < 0.95",
        },
    }
    plan["plan_sha256"] = digest_text(
        json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    write_json(plan_path, plan)
    return plan


def artifact_manifest_path(root: Path, market_date: str, ticker: str) -> Path:
    return root / "manifests" / market_date / f"{ticker}.json"


def load_source_capabilities(path: Path) -> tuple[dict, dict[str, dict]]:
    """Load an audited source-capability contract without changing the data plan."""
    if not path.exists():
        return {}, {}
    contract = json.loads(path.read_text(encoding="utf-8"))
    if contract.get("source") != SOURCE:
        raise RuntimeError(
            f"source capability contract mismatch: {contract.get('source')} != {SOURCE}"
        )
    if contract.get("dataset") != DATASET:
        raise RuntimeError(
            "dataset capability contract mismatch: "
            f"{contract.get('dataset')} != {DATASET}"
        )
    unavailable = {}
    for item in contract.get("terminal_unavailable_symbols", []):
        ticker = str(item.get("ticker", "")).strip().upper()
        if not ticker:
            raise RuntimeError("terminal_unavailable_symbols contains an empty ticker")
        unavailable[ticker] = item
    return contract, unavailable


def write_terminal_unavailable_manifest(
    root: Path,
    *,
    ticker: str,
    market_date: str,
    capability_path: Path,
    capability_sha256: str,
    capability: dict,
) -> Path:
    """Finalize a known source-unavailable ticker/date without fabricating bars."""
    manifest_path = artifact_manifest_path(root, market_date, ticker)
    write_json(
        manifest_path,
        {
            "source": SOURCE,
            "dataset": DATASET,
            "ticker": ticker,
            "market_date": market_date,
            "collection_status": "SOURCE_UNSUPPORTED",
            "reason_code": capability.get("reason_code", "SOURCE_CAPABILITY_UNAVAILABLE"),
            "reason_detail": capability.get("reason_detail"),
            "coverage": None,
            "row_count": 0,
            "first_timestamp": None,
            "last_timestamp": None,
            "raw": None,
            "normalized": None,
            "missing_value_policy": {
                "bars_imputed": False,
                "return_imputed": False,
                "volume_imputed": False,
                "eligible_for_signal": False,
            },
            "source_capability_contract": {
                "artifact": artifact_label(capability_path),
                "sha256": capability_sha256,
            },
            "finalized_at_utc": utc_now(),
            "credential_values_persisted": False,
            "order_or_account_endpoints_called": False,
            "production_connected": False,
            "champion_connected": False,
        },
    )
    return manifest_path


def read_calendar(
    client: ResearchMarketDataClient,
    root: Path,
    market_date: str,
) -> tuple[dict, Path]:
    path = root / "raw" / "calendar" / f"{market_date}.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))["response"]
    else:
        payload = client.get("/api/v1/market-calendar/US", {"date": market_date})
        write_json(
            path,
            {
                "source": SOURCE,
                "downloaded_at_utc": utc_now(),
                "market_date": market_date,
                "response": payload,
            },
        )
    return payload, path


def coverage(frame: pd.DataFrame, bounds: dict) -> dict[str, dict]:
    result = {}
    for session in SESSIONS:
        start, end = bounds[session]
        expected = int((end - start).total_seconds() // 60)
        subset = frame[frame["session"] == session] if not frame.empty else frame
        observed = int(len(subset))
        result[session] = {
            "expected_bars": expected,
            "observed_bars": observed,
            "coverage_ratio": observed / expected if expected else None,
            "missing_ratio": (expected - observed) / expected if expected else None,
            "first_timestamp": (
                subset["bar_start_utc"].min().isoformat() if observed else None
            ),
            "last_timestamp": (
                subset["bar_start_utc"].max().isoformat() if observed else None
            ),
        }
    result["source_missing_suspected"] = (
        result["REGULAR"]["coverage_ratio"] < 0.95
    )
    result["extended_missing_interpretation"] = (
        "SOURCE_MISSING_SUSPECTED"
        if result["source_missing_suspected"]
        else "NO_TRADES_OR_SPARSE_TRADING_POSSIBLE"
    )
    return result


def collect_one(
    client: ResearchMarketDataClient,
    root: Path,
    *,
    ticker: str,
    market_date: str,
    page_size: int,
) -> Path:
    payload, calendar_path = read_calendar(client, root, market_date)
    bounds = session_bounds(calendar_day(payload, market_date))
    pages, rows = collect_extended(
        client, symbol=ticker, bounds=bounds, page_size=page_size
    )
    downloaded_at = utc_now()
    raw_path = root / "raw" / "minute" / market_date / f"{ticker}.json"
    write_json(
        raw_path,
        {
            "source": SOURCE,
            "dataset": DATASET,
            "scope": "HISTORICAL_EXPLORATORY_ONLY",
            "ticker": ticker,
            "market_date": market_date,
            "downloaded_at_utc": downloaded_at,
            "timestamp_convention": "TOSS_TIMESTAMP_RESEARCH_PROVISIONAL_V1",
            "pages": pages,
        },
    )
    frame = normalize_toss(rows, bounds)
    if not frame.empty:
        frame.insert(0, "source", SOURCE)
        frame.insert(1, "ticker", ticker)
        frame.insert(2, "market_date", market_date)
        frame.insert(3, "research_scope", "HISTORICAL_EXPLORATORY_ONLY")
        frame.insert(4, "survivorship_bias_possible", True)
    normalized_path = root / "normalized" / market_date / f"{ticker}.csv"
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(normalized_path, index=False, lineterminator="\n")
    session_coverage = coverage(frame, bounds)
    manifest_path = artifact_manifest_path(root, market_date, ticker)
    write_json(
        manifest_path,
        {
            "source": SOURCE,
            "dataset": DATASET,
            "ticker": ticker,
            "market_date": market_date,
            "sessions": list(SESSIONS),
            "coverage": session_coverage,
            "row_count": int(len(frame)),
            "first_timestamp": (
                frame["bar_start_utc"].min().isoformat() if not frame.empty else None
            ),
            "last_timestamp": (
                frame["bar_start_utc"].max().isoformat() if not frame.empty else None
            ),
            "downloaded_at_utc": downloaded_at,
            "raw": {
                "artifact": str(raw_path.relative_to(ROOT)),
                "sha256": sha256(raw_path),
            },
            "normalized": {
                "artifact": str(normalized_path.relative_to(ROOT)),
                "sha256": sha256(normalized_path),
            },
            "calendar": {
                "artifact": str(calendar_path.relative_to(ROOT)),
                "sha256": sha256(calendar_path),
            },
            "credential_values_persisted": False,
            "order_or_account_endpoints_called": False,
        },
    )
    return manifest_path


def summarize(
    root: Path,
    plan: dict,
    requests: int,
    retries: int,
    *,
    capability_path: Path,
    capability_sha256: str | None,
) -> dict:
    completed_by_date = {}
    data_by_date = {}
    terminal_by_date = {}
    for market_date in plan["market_dates"]:
        data_count = 0
        terminal_count = 0
        for manifest_path in (root / "manifests" / market_date).glob("*.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if manifest.get("collection_status") == "SOURCE_UNSUPPORTED":
                terminal_count += 1
            else:
                data_count += 1
        data_by_date[market_date] = data_count
        terminal_by_date[market_date] = terminal_count
        completed_by_date[market_date] = data_count + terminal_count
    complete_dates = [
        date for date, count in completed_by_date.items() if count == plan["ticker_count"]
    ]
    complete_data_dates = [
        date for date, count in data_by_date.items() if count == plan["ticker_count"]
    ]
    completed = sum(completed_by_date.values())
    data_artifacts = sum(data_by_date.values())
    terminal_missing = sum(terminal_by_date.values())
    status = (
        "READY_EXPLORATORY"
        if len(complete_dates) >= plan["minimum_sessions"]
        else "COLLECTING"
    )
    value = {
        "status": status,
        "dataset": DATASET,
        "scope": "HISTORICAL_EXPLORATORY_ONLY",
        "flags": plan["flags"],
        "plan_sha256": plan["plan_sha256"],
        "ticker_count": plan["ticker_count"],
        "target_sessions": plan["target_sessions"],
        "minimum_sessions": plan["minimum_sessions"],
        "total_ticker_dates": plan["total_ticker_dates"],
        "completed_ticker_dates": completed,
        "data_artifact_ticker_dates": data_artifacts,
        "terminal_missing_ticker_dates": terminal_missing,
        "completion_ratio": completed / plan["total_ticker_dates"],
        "complete_sessions": len(complete_dates),
        "complete_data_sessions": len(complete_data_dates),
        "requests_this_run": requests,
        "http_429_retries_this_run": retries,
        "premarket_exploratory_allowed": len(complete_dates) >= plan["minimum_sessions"],
        "confirmatory_use_allowed": False,
        "champion_connected": False,
        "production_connected": False,
        "source_capability_contract": {
            "artifact": artifact_label(capability_path),
            "sha256": capability_sha256,
        },
        "updated_at_utc": utc_now(),
    }
    write_json(root / "dataset_status.json", value)
    return value


def main() -> int:
    args = parse_args()
    root = (ROOT / args.output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".backfill.lock"
    lock_handle = lock_path.open("w")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(json.dumps({"status": "LOCK_BUSY", "lock": str(lock_path)}))
        return 75
    plan_path = root / "dataset_plan.json"
    plan = (
        json.loads(plan_path.read_text(encoding="utf-8"))
        if plan_path.exists()
        else initialize_plan(
            plan_path=plan_path,
            config_path=(ROOT / args.config).resolve(),
            daily_cache=(ROOT / args.daily_cache).resolve(),
            target_sessions=args.target_sessions,
            minimum_sessions=args.minimum_sessions,
        )
    )
    capability_path = (ROOT / args.source_capabilities).resolve()
    _, terminal_unavailable = load_source_capabilities(capability_path)
    capability_sha256 = sha256(capability_path) if capability_path.exists() else None
    if args.finalize_terminal_capabilities_only:
        terminalized = 0
        for market_date in plan["market_dates"]:
            for ticker in plan["tickers"]:
                capability = terminal_unavailable.get(ticker.upper())
                manifest = artifact_manifest_path(root, market_date, ticker)
                if capability is None or manifest.exists():
                    continue
                write_terminal_unavailable_manifest(
                    root,
                    ticker=ticker,
                    market_date=market_date,
                    capability_path=capability_path,
                    capability_sha256=capability_sha256 or "",
                    capability=capability,
                )
                terminalized += 1
        status = summarize(
            root,
            plan,
            requests=0,
            retries=0,
            capability_path=capability_path,
            capability_sha256=capability_sha256,
        )
        status["processed_this_run"] = terminalized
        status["terminalized_this_run"] = terminalized
        status["errors_this_run"] = []
        write_json(root / "last_run.json", status)
        print(json.dumps(status, ensure_ascii=False))
        return 0
    client = ResearchMarketDataClient(
        Path(args.python_stock_root),
        timeout=args.timeout,
        max_retries=args.max_retries,
        request_spacing=args.request_spacing,
    )
    processed = 0
    terminalized = 0
    errors = []
    for market_date in plan["market_dates"]:
        for ticker in plan["tickers"]:
            manifest = artifact_manifest_path(root, market_date, ticker)
            if manifest.exists():
                continue
            capability = terminal_unavailable.get(ticker.upper())
            if capability is not None:
                write_terminal_unavailable_manifest(
                    root,
                    ticker=ticker,
                    market_date=market_date,
                    capability_path=capability_path,
                    capability_sha256=capability_sha256 or "",
                    capability=capability,
                )
                processed += 1
                terminalized += 1
                if processed >= args.max_ticker_dates:
                    break
                continue
            try:
                collect_one(
                    client,
                    root,
                    ticker=ticker,
                    market_date=market_date,
                    page_size=args.page_size,
                )
                processed += 1
            except Exception as exc:
                errors.append(
                    {
                        "ticker": ticker,
                        "market_date": market_date,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:300],
                    }
                )
                if len(errors) >= 5:
                    break
            if processed >= args.max_ticker_dates:
                break
        if processed >= args.max_ticker_dates or len(errors) >= 5:
            break
    status = summarize(
        root,
        plan,
        client.request_count,
        client.rate_limit_retries,
        capability_path=capability_path,
        capability_sha256=capability_sha256,
    )
    status["processed_this_run"] = processed
    status["terminalized_this_run"] = terminalized
    status["errors_this_run"] = errors
    write_json(root / "last_run.json", status)
    print(json.dumps(status, ensure_ascii=False))
    return 1 if errors and not processed else 0


if __name__ == "__main__":
    raise SystemExit(main())
