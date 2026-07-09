#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any


OK = 0
ERROR = 1
FAILED = 2


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Phoenix Quant validation artifacts for leakage-prone setup errors.")
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--summary-csv", default=None)
    parser.add_argument("--oos-rules-csv", default=None)
    parser.add_argument("--write-json", default=None)
    parser.add_argument("--max-test-end-lag-days", type=int, default=14)
    return parser.parse_args()


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _find_latest(base: Path, pattern: str) -> Path | None:
    paths = [p for p in base.glob(pattern) if p.is_file()]
    if not paths:
        return None
    return max(paths, key=lambda p: (p.stat().st_mtime, str(p)))


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _float(value: Any, default: float = math.nan) -> float:
    if value is None or value == "":
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _int(value: Any, default: int = 0) -> int:
    parsed = _float(value, math.nan)
    return int(parsed) if math.isfinite(parsed) else default


def _add_check(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


def _rel_or_abs(path_value: str | None, base: Path) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    if path.is_absolute():
        return path
    candidate_relative = base / path
    if candidate_relative.exists():
        return candidate_relative
    return Path.cwd() / path


def _audit(candidate_dir: Path, summary_csv: Path, oos_csv: Path, max_test_end_lag_days: int) -> dict[str, Any]:
    summary_rows = _read_csv_rows(summary_csv)
    if not summary_rows:
        raise ValueError(f"Summary CSV is empty: {summary_csv}")
    oos_rows = _read_csv_rows(oos_csv)
    if not oos_rows:
        raise ValueError(f"OOS rules CSV is empty: {oos_csv}")

    summary = summary_rows[0]
    checks: list[dict[str, Any]] = []

    train_start = _parse_date(summary.get("train_start"))
    train_end = _parse_date(summary.get("train_end"))
    test_start = _parse_date(summary.get("test_start"))
    test_end = _parse_date(summary.get("test_end"))
    min_test_start = _parse_date(summary.get("min_test_start_after_embargo"))
    train_last = _parse_date(summary.get("train_last_trading_day"))

    _add_check(
        checks,
        "date_fields_present",
        all([train_start, train_end, test_start, test_end]),
        f"train={train_start}..{train_end} test={test_start}..{test_end}",
    )
    if train_start and train_end:
        _add_check(checks, "train_window_order", train_start <= train_end, f"{train_start} <= {train_end}")
    if test_start and test_end:
        _add_check(checks, "test_window_order", test_start <= test_end, f"{test_start} <= {test_end}")
    if train_end and test_start:
        _add_check(checks, "train_test_separation", train_end < test_start, f"{train_end} < {test_start}")
    if min_test_start and test_start:
        _add_check(checks, "embargo_respected", test_start >= min_test_start, f"test_start={test_start} min={min_test_start}")
    if train_last and train_end:
        _add_check(checks, "train_last_within_train_end", train_last <= train_end, f"train_last={train_last} train_end={train_end}")
    if test_end:
        lag_days = (date.today() - test_end).days
        _add_check(
            checks,
            "test_end_not_future",
            lag_days >= 0,
            f"test_end={test_end} today={date.today()} lag_days={lag_days}",
        )
        _add_check(
            checks,
            "test_end_recent_enough",
            lag_days <= max_test_end_lag_days,
            f"test_end={test_end} lag_days={lag_days} max={max_test_end_lag_days}",
        )

    rank_mode = str(summary.get("rank_mode") or "")
    _add_check(checks, "rank_mode_recorded", bool(rank_mode), f"rank_mode={rank_mode or 'missing'}")

    train_report_dir = _rel_or_abs(summary.get("train_report_dir"), candidate_dir)
    test_report_dir = _rel_or_abs(summary.get("test_report_dir"), candidate_dir)
    _add_check(checks, "train_report_dir_exists", bool(train_report_dir and train_report_dir.exists()), str(train_report_dir))
    _add_check(checks, "test_report_dir_exists", bool(test_report_dir and test_report_dir.exists()), str(test_report_dir))
    if train_report_dir and test_report_dir:
        _add_check(checks, "train_test_report_dirs_distinct", train_report_dir != test_report_dir, f"train={train_report_dir} test={test_report_dir}")

    min_oos_rank = min(_int(row.get("oos_rank"), 10**9) for row in oos_rows)
    _add_check(checks, "oos_rule_rank_present", min_oos_rank < 10**9, f"best_oos_rank={min_oos_rank}")

    passed = all(check["passed"] for check in checks)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "candidate_dir": str(candidate_dir),
        "passed": passed,
        "summary_csv": str(summary_csv),
        "oos_rules_csv": str(oos_csv),
        "checks": checks,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def main() -> int:
    args = _parse_args()
    candidate_dir = Path(args.candidate_dir).resolve()
    output_path = Path(args.write_json).resolve() if args.write_json else candidate_dir / "leakage_audit.json"
    try:
        summary_csv = Path(args.summary_csv).resolve() if args.summary_csv else _find_latest(
            candidate_dir, "reports/benchmark_train_test_*/benchmark_train_test_summary.csv"
        )
        oos_csv = Path(args.oos_rules_csv).resolve() if args.oos_rules_csv else _find_latest(
            candidate_dir, "reports/benchmark_train_test_*/benchmark_oos_rules.csv"
        )
        if summary_csv is None:
            raise FileNotFoundError(f"benchmark_train_test_summary.csv not found under {candidate_dir}")
        if oos_csv is None:
            raise FileNotFoundError(f"benchmark_oos_rules.csv not found under {candidate_dir}")
        payload = _audit(candidate_dir, summary_csv, oos_csv, args.max_test_end_lag_days)
        _write_json(output_path, payload)
        if payload["passed"]:
            print(f"LEAKAGE_AUDIT_PASSED {output_path}")
            return OK
        print(f"LEAKAGE_AUDIT_FAILED {output_path}")
        for check in payload["checks"]:
            if not check["passed"]:
                print(f"- {check['name']}: {check['detail']}")
        return FAILED
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return ERROR


if __name__ == "__main__":
    raise SystemExit(main())
