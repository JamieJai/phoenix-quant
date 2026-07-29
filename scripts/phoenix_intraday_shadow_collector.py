#!/usr/bin/env python3
"""Collect frozen-universe intraday shadow rows without Telegram or broker use."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from phoenix_core.engines.intraday_context_engine import (  # noqa: E402
    IntradayContextEngine,
)
from phoenix_core.intraday_feature_store import (  # noqa: E402
    append_intraday_feature_rows,
)

NY = ZoneInfo("America/New_York")
PREREG = "research/preregistrations/PAPER_INTRADAY_SAMPLING_V1.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_timestamp(value: str) -> datetime | None:
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        return None
    return timestamp.astimezone(timezone.utc)


def collect(
    *,
    universe_dir: str,
    cache: str,
    maximum_bar_age_seconds: int,
    future_tolerance_seconds: int,
) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    market_date = now.astimezone(NY).date().isoformat()
    universe_path = ROOT / universe_dir / f"{market_date}.json"
    if not universe_path.exists():
        return {
            "status": "BLOCKED",
            "reason": "PIT_UNIVERSE_MISSING",
            "market_date": market_date,
            "accepted": 0,
            "telegram_sent": False,
            "broker_routes_called": False,
        }
    universe = json.loads(universe_path.read_text(encoding="utf-8"))
    known_at = _parse_timestamp(universe.get("known_at_utc", ""))
    if (
        universe.get("status") != "IMMUTABLE"
        or known_at is None
        or known_at > now
    ):
        return {
            "status": "BLOCKED",
            "reason": "PIT_UNIVERSE_INVALID",
            "market_date": market_date,
            "accepted": 0,
            "telegram_sent": False,
            "broker_routes_called": False,
        }
    tickers = list(dict.fromkeys(universe.get("tickers", [])))[:5]
    engine = IntradayContextEngine(
        include_prepost=True,
        completed_bars_only=True,
    )
    contexts = engine.analyze_many(tickers)
    accepted = []
    rejected = []
    for context in contexts:
        timestamp = _parse_timestamp(context.timestamp)
        if timestamp is None:
            rejected.append({"ticker": context.ticker, "reason": "TIMESTAMP_INVALID"})
            continue
        age = (now - timestamp).total_seconds()
        if age < -future_tolerance_seconds:
            rejected.append({"ticker": context.ticker, "reason": "FUTURE_BAR"})
            continue
        if age > maximum_bar_age_seconds:
            rejected.append(
                {
                    "ticker": context.ticker,
                    "reason": "STALE_BAR",
                    "age_seconds": round(age, 1),
                }
            )
            continue
        if context.label in {"NO_DATA", "DATA_ERROR"}:
            rejected.append({"ticker": context.ticker, "reason": context.label})
            continue
        accepted.append(context)
    appended = (
        append_intraday_feature_rows(
            accepted,
            ROOT / cache,
            dedupe_keys=("ticker", "timestamp", "source"),
        )
        if accepted
        else 0
    )
    return {
        "status": "COLLECTED" if appended else "NO_FRESH_ROWS",
        "market_date": market_date,
        "known_at_utc": universe.get("known_at_utc"),
        "universe_artifact": str(universe_path.relative_to(ROOT)),
        "universe_sha256": sha256(universe_path),
        "requested_tickers": tickers,
        "accepted": appended,
        "rejected": rejected,
        "cache": cache,
        "preregistration": {
            "artifact": PREREG,
            "sha256": sha256(ROOT / PREREG),
        },
        "telegram_sent": False,
        "network_source": "YFINANCE_MARKET_DATA_ONLY",
        "broker_routes_called": False,
        "account_endpoints_called": False,
        "production_score_changed": False,
        "champion_changed": False,
        "live_enabled": False,
        "generated_at_utc": now.isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--universe-dir",
        default="data/research/phoenix_paper_signal_universe",
    )
    parser.add_argument("--cache", default="data/intraday_features.csv")
    parser.add_argument("--maximum-bar-age-seconds", type=int, default=1800)
    parser.add_argument("--future-tolerance-seconds", type=int, default=60)
    parser.add_argument(
        "--report-dir",
        default="reports/paper_trading/shadow_collection",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = collect(
        universe_dir=args.universe_dir,
        cache=args.cache,
        maximum_bar_age_seconds=args.maximum_bar_age_seconds,
        future_tolerance_seconds=args.future_tolerance_seconds,
    )
    report_dir = ROOT / args.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report = report_dir / f"shadow_collection_{stamp}.json"
    report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    payload = {
        **result,
        "report": str(report.relative_to(ROOT)),
        "report_sha256": sha256(report),
    }
    print(
        json.dumps(payload, ensure_ascii=False)
        if args.json
        else f"{result['status']} accepted={result['accepted']}"
    )
    return 0 if result["status"] in {"COLLECTED", "NO_FRESH_ROWS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
