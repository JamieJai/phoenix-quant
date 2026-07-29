#!/usr/bin/env python3
"""Write one immutable, research-only Phoenix universe snapshot per US session."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from phoenix_core.config import load_config
from phoenix_toss_us_capability_audit import (
    ROOT,
    ResearchMarketDataClient,
    calendar_day,
    session_bounds,
)


NY = ZoneInfo("America/New_York")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--market-date", default="")
    parser.add_argument("--output-root", default="data/research/phoenix_pit_universe")
    parser.add_argument("--python-stock-root", default="/home/sysadmin/python-stock")
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    known_at = datetime.now(timezone.utc)
    market_date = args.market_date or known_at.astimezone(NY).date().isoformat()
    config_path = (ROOT / args.config).resolve()
    output_root = (ROOT / args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    artifact_path = output_root / f"{market_date}.csv"
    manifest_path = output_root / f"{market_date}.manifest.json"

    if artifact_path.exists() or manifest_path.exists():
        if not artifact_path.exists() or not manifest_path.exists():
            raise RuntimeError("immutable PIT snapshot is partially present")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if digest(artifact_path) != manifest.get("artifact_sha256"):
            raise RuntimeError("immutable PIT snapshot checksum mismatch")
        print(
            json.dumps(
                {
                    "status": "IMMUTABLE_EXISTS",
                    "market_date": market_date,
                    "artifact": str(artifact_path),
                    "sha256": manifest["artifact_sha256"],
                }
            )
        )
        return 0

    client = ResearchMarketDataClient(
        Path(args.python_stock_root),
        timeout=args.timeout,
        max_retries=4,
        request_spacing=0.25,
    )
    calendar_payload = client.get("/api/v1/market-calendar/US", {"date": market_date})
    day = calendar_day(calendar_payload, market_date)
    bounds = session_bounds(day)
    regular_start, regular_end = bounds["REGULAR"]
    cutoff = pd.Timestamp(f"{market_date} 09:25:00", tz="America/New_York")

    config = load_config(str(config_path))
    stock_set = set(config.universe)
    tickers = list(dict.fromkeys([*config.universe, *config.market_etfs, "SPY", "QQQ", "SMH", "SOXX"]))
    config_sha = digest(config_path)
    selection_rule_version = f"phoenix_config_sha256:{config_sha}"
    rows = [
        {
            "market_date": market_date,
            "ticker": ticker,
            "eligible": True,
            "universe_component": (
                "PHOENIX_STOCK_UNIVERSE" if ticker in stock_set else "PHOENIX_CONTEXT_ETF"
            ),
            "selection_rule_version": selection_rule_version,
            "known_at_utc": known_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "known_before_0925_et": known_at <= cutoff.to_pydatetime(),
            "source": "config/config.yaml",
            "evidence_level": "LEVEL_A_ACTUAL_ARTIFACT",
        }
        for ticker in tickers
    ]
    frame = pd.DataFrame(rows).sort_values("ticker")
    frame.to_csv(artifact_path, index=False, lineterminator="\n")
    artifact_sha = digest(artifact_path)
    manifest = {
        "status": "IMMUTABLE",
        "source": "PHOENIX_PIT_UNIVERSE",
        "market_date": market_date,
        "known_at_utc": known_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "known_before_0925_et": known_at <= cutoff.to_pydatetime(),
        "selection_rule_version": selection_rule_version,
        "ticker_count": len(frame),
        "stock_count": int((frame["universe_component"] == "PHOENIX_STOCK_UNIVERSE").sum()),
        "context_etf_count": int((frame["universe_component"] == "PHOENIX_CONTEXT_ETF").sum()),
        "market_calendar_source": "TOSS_US_MARKET_CALENDAR",
        "regular_start": regular_start.isoformat(),
        "regular_end": regular_end.isoformat(),
        "artifact": str(artifact_path.relative_to(ROOT)),
        "artifact_format": "CSV",
        "parquet_unavailable_reason": "pyarrow/fastparquet is not installed in the project environment",
        "artifact_sha256": artifact_sha,
        "credential_values_persisted": False,
    }
    write_json(manifest_path, manifest)
    print(
        json.dumps(
            {
                "status": "CREATED",
                "market_date": market_date,
                "ticker_count": len(frame),
                "known_before_0925_et": manifest["known_before_0925_et"],
                "artifact": str(artifact_path),
                "sha256": artifact_sha,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
