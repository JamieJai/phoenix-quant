#!/usr/bin/env python3
"""Validate durable shadow state, idempotency, and conservative exits offline."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from phoenix_core.trade.shadow_ledger import (  # noqa: E402
    ShadowBar,
    ShadowLedger,
    ShadowQuote,
)

PREREG = ROOT / (
    "research/preregistrations/STATEFUL_SHADOW_PORTFOLIO_V1.json"
)
IMPLEMENTATION = ROOT / "phoenix_core/trade/shadow_ledger.py"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _row(ticker: str, now: datetime) -> dict[str, object]:
    return {
        "ticker": ticker,
        "recorded_at": now.isoformat(),
        "timestamp": (now - timedelta(minutes=10)).isoformat(),
        "source": "yfinance",
        "data_confidence_score": 80.0,
        "intraday_risk_score": 40.0,
    }


def validate() -> dict[str, object]:
    now = datetime(2026, 7, 29, 14, 0, 20, tzinfo=timezone.utc)
    with TemporaryDirectory() as directory:
        database = Path(directory) / "shadow.sqlite3"
        first = ShadowLedger(database)
        quote = ShadowQuote(
            "AAPL",
            100.0,
            now - timedelta(seconds=20),
        )
        opened = first.process_signal(_row("AAPL", now), quote, now=now)
        duplicate = first.process_signal(_row("AAPL", now), quote, now=now)
        second_symbol_position = first.process_signal(
            _row("AAPL", now + timedelta(seconds=1)),
            quote,
            now=now + timedelta(seconds=1),
        )

        restarted = ShadowLedger(database)
        recovered = restarted.snapshot()
        stop = float(opened.get("stop_price", 0.0))
        target = float(opened.get("target_price", 0.0))
        closed = restarted.process_bars(
            "AAPL",
            [
                ShadowBar(
                    ticker="AAPL",
                    bar_start=now,
                    available_at=now + timedelta(minutes=1),
                    open=100.0,
                    high=target + 0.1,
                    low=stop - 0.1,
                    close=100.0,
                )
            ],
            now=now + timedelta(minutes=1),
        )
        closed_snapshot = ShadowLedger(database).snapshot()
        stale = restarted.process_signal(
            _row("MSFT", now),
            ShadowQuote(
                "MSFT",
                100.0,
                now - timedelta(seconds=31),
            ),
            now=now,
        )
        final = restarted.snapshot()

    checks = {
        "valid_signal_opens_position": opened.get("status") == "OPENED",
        "duplicate_signal_is_idempotent": duplicate.get("status") == "DUPLICATE",
        "duplicate_symbol_position_is_blocked": (
            second_symbol_position.get("status") == "REJECTED"
            and "SYMBOL_POSITION_LIMIT"
            in second_symbol_position.get("reasons", [])
        ),
        "restart_recovers_open_position": (
            recovered.get("positions", {}).get("OPEN") == 1
        ),
        "same_bar_stop_target_is_stop_first": (
            len(closed) == 1
            and closed[0].get("exit_reason") == "STOP_FIRST"
            and float(closed[0].get("net_return", 0.0)) < 0
        ),
        "closed_state_survives_restart": (
            closed_snapshot.get("positions", {}).get("CLOSED") == 1
        ),
        "stale_quote_fails_closed": (
            stale.get("status") == "REJECTED"
            and "QUOTE_STALE" in stale.get("reasons", [])
        ),
        "event_log_persisted": int(final.get("event_count", 0)) >= 3,
        "no_broker_or_account_route": (
            final.get("broker_routes_called") is False
            and final.get("account_endpoints_called") is False
            and final.get("live_enabled") is False
        ),
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "evidence_type": "STATEFUL_SHADOW_PORTFOLIO_VALIDATION_V1",
        "validated_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z"),
        "checks": checks,
        "preregistration": {
            "artifact": str(PREREG.relative_to(ROOT)),
            "sha256": sha256(PREREG),
        },
        "implementation": {
            "artifact": str(IMPLEMENTATION.relative_to(ROOT)),
            "sha256": sha256(IMPLEMENTATION),
        },
        "network_called": False,
        "broker_routes_called": False,
        "account_endpoints_called": False,
        "live_enabled": False,
        "production_changed": False,
        "champion_changed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=(
            "reports/paper_trading/shadow_ledger/"
            "shadow_ledger_validation_latest.json"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = validate()
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    payload = {
        **result,
        "artifact": str(output.relative_to(ROOT)),
        "artifact_sha256": sha256(output),
    }
    print(
        json.dumps(payload, ensure_ascii=False)
        if args.json
        else result["status"]
    )
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
