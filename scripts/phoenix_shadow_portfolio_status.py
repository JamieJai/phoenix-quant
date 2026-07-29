#!/usr/bin/env python3
"""Read-only status for the durable broker-free shadow portfolio."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from phoenix_core.trade.shadow_ledger import ShadowLedger  # noqa: E402


def status(database: str, report: str) -> dict[str, object]:
    database_path = ROOT / database
    report_path = ROOT / report
    if not database_path.exists():
        return {
            "status": "EXPERIMENTAL",
            "operational_health": "DEGRADED",
            "reason": "SHADOW_DATABASE_MISSING",
            "broker_routes_called": False,
            "live_enabled": False,
        }
    snapshot = ShadowLedger(database_path).snapshot()
    latest = {}
    if report_path.exists():
        try:
            latest = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            latest = {"status": "INVALID_REPORT"}
    operational = (
        "DEGRADED"
        if latest.get("status") in {"DEGRADED", "INVALID_REPORT"}
        else "HEALTHY"
    )
    return {
        "status": "EXPERIMENTAL",
        "operational_health": operational,
        "worker_status": latest.get("status", "REPORT_MISSING"),
        "database": database,
        "report": report,
        "ledger": snapshot,
        "broker_routes_called": False,
        "account_endpoints_called": False,
        "live_enabled": False,
        "production_connected": False,
        "champion_connected": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=(
            "data/research/paper_shadow_portfolio/"
            "shadow_portfolio_v1.sqlite3"
        ),
    )
    parser.add_argument(
        "--report",
        default=(
            "reports/paper_trading/shadow_ledger/"
            "shadow_portfolio_latest.json"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = status(args.database, args.report)
    print(
        json.dumps(result, ensure_ascii=False)
        if args.json
        else (
            f"{result['status']} "
            f"health={result['operational_health']}"
        )
    )
    return 0 if result["operational_health"] == "HEALTHY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
