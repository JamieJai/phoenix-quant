#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


RETURN_FIELDS = ["observed_1d_return", "observed_5d_return", "observed_10d_return"]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize Phoenix Quant operator feedback CSV.")
    parser.add_argument("--feedback-csv", default="data/operator_feedback.csv")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def _float(value: Any, default: float = math.nan) -> float:
    if value is None or value == "":
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _mean(values: list[float]) -> float | None:
    clean = [v for v in values if math.isfinite(v)]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _fmt_pct(value: Any) -> str:
    val = _float(value)
    return "n/a" if not math.isfinite(val) else f"{val * 100:.2f}%"


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _summarize(rows: list[dict[str, str]]) -> dict[str, Any]:
    by_label: Counter[str] = Counter()
    by_reason: Counter[str] = Counter()
    by_ticker: Counter[str] = Counter()
    returns_by_label: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    for row in rows:
        label = (row.get("outcome_label") or "unlabeled").strip() or "unlabeled"
        reason = (row.get("reason_category") or "uncategorized").strip() or "uncategorized"
        ticker = (row.get("ticker") or "").strip().upper()
        by_label[label] += 1
        by_reason[reason] += 1
        if ticker:
            by_ticker[ticker] += 1
        for field in RETURN_FIELDS:
            returns_by_label[label][field].append(_float(row.get(field)))

    return {
        "n_rows": len(rows),
        "by_label": dict(by_label.most_common()),
        "by_reason": dict(by_reason.most_common()),
        "top_tickers": dict(by_ticker.most_common(20)),
        "returns_by_label": {
            label: {field: _mean(values) for field, values in fields.items()}
            for label, fields in returns_by_label.items()
        },
    }


def _print_summary(path: Path, summary: dict[str, Any]) -> None:
    print("Phoenix Operator Feedback Summary")
    print("=================================")
    print(f"file: {path}")
    print(f"rows: {summary['n_rows']}")
    if summary["n_rows"] == 0:
        print("No feedback rows yet. Start from config/operator_feedback.csv.example and save as data/operator_feedback.csv.")
        return

    print("\nOutcome labels:")
    for label, count in summary["by_label"].items():
        print(f"- {label}: {count}")

    print("\nReason categories:")
    for reason, count in summary["by_reason"].items():
        print(f"- {reason}: {count}")

    print("\nAverage returns by label:")
    for label, fields in summary["returns_by_label"].items():
        print(
            f"- {label}: "
            f"1D={_fmt_pct(fields.get('observed_1d_return'))} "
            f"5D={_fmt_pct(fields.get('observed_5d_return'))} "
            f"10D={_fmt_pct(fields.get('observed_10d_return'))}"
        )

    print("\nMost frequent tickers:")
    for ticker, count in list(summary["top_tickers"].items())[:10]:
        print(f"- {ticker}: {count}")


def main() -> int:
    args = _parse_args()
    path = Path(args.feedback_csv)
    summary = _summarize(_read_rows(path))
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_summary(path, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
