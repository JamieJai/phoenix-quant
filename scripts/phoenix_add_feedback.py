#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path
from typing import Any


FIELDS = [
    "feedback_date",
    "as_of_date",
    "ticker",
    "source",
    "rank",
    "decision",
    "outcome_label",
    "observed_1d_return",
    "observed_5d_return",
    "observed_10d_return",
    "reason_category",
    "market_context",
    "notes",
]

DECISIONS = {"watch", "skip", "avoid", "hold", "review"}
LABELS = {"good", "bad", "neutral", "skip", "unknown"}
REASONS = {
    "trend_following",
    "market_regime",
    "sector_context",
    "earnings_or_event",
    "liquidity_gap",
    "overextended",
    "false_similarity",
    "risk_warning_missing",
    "other",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append one Phoenix Quant operator feedback row.")
    parser.add_argument("--feedback-csv", default="data/operator_feedback.csv")
    parser.add_argument("--feedback-date", default=date.today().isoformat())
    parser.add_argument("--as-of", required=True, dest="as_of_date", help="Candidate as-of date YYYY-MM-DD")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--source", default="telegram_top")
    parser.add_argument("--rank", type=int, default=0)
    parser.add_argument("--decision", choices=sorted(DECISIONS), default="watch")
    parser.add_argument("--label", choices=sorted(LABELS), default="unknown", dest="outcome_label")
    parser.add_argument("--return-1d", default="", dest="observed_1d_return", help="Decimal return such as 0.012 or percent such as 1.2%%")
    parser.add_argument("--return-5d", default="", dest="observed_5d_return", help="Decimal return such as 0.045 or percent such as 4.5%%")
    parser.add_argument("--return-10d", default="", dest="observed_10d_return", help="Decimal return such as 0.071 or percent such as 7.1%%")
    parser.add_argument("--reason", choices=sorted(REASONS), default="other", dest="reason_category")
    parser.add_argument("--market-context", default="")
    parser.add_argument("--notes", default="")
    return parser.parse_args()


def _parse_date(value: str, field: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field} must be YYYY-MM-DD: {value}") from exc


def _parse_return(value: Any) -> str:
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    is_percent = raw.endswith("%")
    if is_percent:
        raw = raw[:-1].strip()
    try:
        parsed = float(raw)
    except ValueError as exc:
        raise ValueError(f"return value must be numeric or percent: {value}") from exc
    if is_percent:
        parsed /= 100.0
    return f"{parsed:.10g}"


def _read_header(path: Path) -> list[str] | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        try:
            return next(reader)
        except StopIteration:
            return None


def _ensure_header(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = _read_header(path)
    if header is None:
        with path.open("w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(FIELDS)
        return
    if header != FIELDS:
        raise ValueError(
            f"Unexpected feedback CSV header in {path}.\n"
            f"Expected: {FIELDS}\n"
            f"Actual:   {header}"
        )


def _build_row(args: argparse.Namespace) -> dict[str, str]:
    ticker = args.ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker is required")
    rank = int(args.rank)
    if rank < 0:
        raise ValueError("rank must be >= 0")
    return {
        "feedback_date": _parse_date(args.feedback_date, "feedback_date"),
        "as_of_date": _parse_date(args.as_of_date, "as_of"),
        "ticker": ticker,
        "source": args.source.strip() or "telegram_top",
        "rank": str(rank),
        "decision": args.decision,
        "outcome_label": args.outcome_label,
        "observed_1d_return": _parse_return(args.observed_1d_return),
        "observed_5d_return": _parse_return(args.observed_5d_return),
        "observed_10d_return": _parse_return(args.observed_10d_return),
        "reason_category": args.reason_category,
        "market_context": args.market_context.strip(),
        "notes": args.notes.strip(),
    }


def _append_row(path: Path, row: dict[str, str]) -> None:
    _ensure_header(path)
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writerow(row)


def main() -> int:
    args = _parse_args()
    path = Path(args.feedback_csv)
    try:
        row = _build_row(args)
        _append_row(path, row)
        print(f"FEEDBACK_ADDED {path} {row['as_of_date']} {row['ticker']} label={row['outcome_label']} reason={row['reason_category']}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
