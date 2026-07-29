#!/usr/bin/env python3
"""Freeze a development-only constant expected-return calibration baseline."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
NY = ZoneInfo("America/New_York")
PREREG = ROOT / (
    "research/preregistrations/PAPER_EXPECTED_RETURN_CALIBRATOR_V1.json"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _timestamp(value: str) -> datetime | None:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if result.tzinfo is None:
        return None
    return result.astimezone(timezone.utc)


def freeze(
    fills_path: str,
    output_path: str,
    *,
    prospective_start: str = "2026-07-29",
    minimum_rows: int = 40,
) -> dict[str, object]:
    source = Path(fills_path)
    output = Path(output_path)
    if output.exists():
        existing = json.loads(output.read_text(encoding="utf-8"))
        if (
            existing.get("status") != "FROZEN"
            or existing.get("preregistration_sha256") != sha256(PREREG)
        ):
            raise RuntimeError("existing calibrator fails immutable contract")
        return {
            **existing,
            "immutable_existing": True,
            "artifact": str(output),
            "artifact_sha256": sha256(output),
        }
    with source.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    selected: list[dict[str, object]] = []
    for row in rows:
        timestamp = _timestamp(row.get("signal_timestamp", ""))
        try:
            outcome = float(row.get("forward_return_5m", ""))
        except (TypeError, ValueError):
            continue
        if (
            timestamp is None
            or timestamp.astimezone(NY).date().isoformat()
            >= prospective_start
            or not math.isfinite(outcome)
            or abs(outcome) > 1.0
        ):
            continue
        selected.append(
            {
                "symbol": str(row.get("symbol", "")).upper(),
                "signal_timestamp": timestamp.isoformat(),
                "forward_return_5m": outcome,
            }
        )
    if len(selected) < minimum_rows:
        raise RuntimeError(
            f"insufficient development outcomes: {len(selected)} < {minimum_rows}"
        )
    canonical = json.dumps(
        sorted(
            selected,
            key=lambda row: (
                str(row["signal_timestamp"]),
                str(row["symbol"]),
            ),
        ),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    expected_return = sum(
        float(row["forward_return_5m"]) for row in selected
    ) / len(selected)
    result = {
        "status": "FROZEN",
        "calibrator_id": "PAPER_EXPECTED_RETURN_CALIBRATOR_V1",
        "model_type": "CONSTANT_HISTORICAL_MEAN_GROSS_RETURN",
        "prospective_market_date_start": prospective_start,
        "predicted_return": expected_return,
        "development_rows": len(selected),
        "development_tickers": len(
            {str(row["symbol"]) for row in selected}
        ),
        "development_dates": len(
            {
                str(row["signal_timestamp"])[:10]
                for row in selected
            }
        ),
        "development_rows_sha256": hashlib.sha256(canonical).hexdigest(),
        "source_artifact": str(source),
        "source_artifact_sha256_at_freeze": sha256(source),
        "preregistration": str(PREREG.relative_to(ROOT)),
        "preregistration_sha256": sha256(PREREG),
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z"),
        "historical_interpretation": "DEVELOPMENT_ONLY",
        "selection_or_order_use": False,
        "parameter_retuning_allowed": False,
        "broker_routes_called": False,
        "live_enabled": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        **result,
        "immutable_existing": False,
        "artifact": str(output),
        "artifact_sha256": sha256(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fills",
        default="reports/paper_trading/replay_latest/fills.csv",
    )
    parser.add_argument(
        "--output",
        default=(
            "reports/paper_trading/calibration/"
            "paper_expected_return_calibrator_v1.json"
        ),
    )
    parser.add_argument("--prospective-start", default="2026-07-29")
    parser.add_argument("--minimum-rows", type=int, default=40)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = freeze(
        args.fills,
        args.output,
        prospective_start=args.prospective_start,
        minimum_rows=args.minimum_rows,
    )
    print(
        json.dumps(result, ensure_ascii=False)
        if args.json
        else f"FROZEN rows={result['development_rows']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
