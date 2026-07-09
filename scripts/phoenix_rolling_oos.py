#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


OK = 0
ERROR = 1
FAILED = 2


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run rolling purged OOS validations for Phoenix Quant candidates.")
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--splits", required=True, help="Semicolon list: name:train_start,train_end,test_start,test_end or train_start,train_end,test_start,test_end")
    parser.add_argument("--output-json", default=None)
    parser.add_argument("--python-bin", default=sys.executable)
    parser.add_argument("--benchmark-script", default="benchmark.py")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--period", default="5y")
    parser.add_argument("--frequency", default="monthly")
    parser.add_argument("--random-baseline", type=int, default=1000)
    parser.add_argument("--bootstrap", type=int, default=1000)
    parser.add_argument("--train-top-k-rules", type=int, default=5)
    parser.add_argument("--rank-mode", default="decision", choices=["decision", "ranking", "both"])
    parser.add_argument("--xgb-blend-weight", default="0.0")
    parser.add_argument("--embargo-trading-days", type=int, default=10)
    parser.add_argument("--min-dollar-volume", type=float, default=10_000_000.0)
    parser.add_argument("--min-price", type=float, default=5.0)
    parser.add_argument("--max-gap-open", type=float, default=0.08)
    parser.add_argument("--entry-penalty-bps", type=float, default=20.0)
    parser.add_argument("--max-dates", type=int, default=None)
    parser.add_argument("--min-sample-size", type=int, default=50)
    parser.add_argument("--min-active-trades", type=int, default=30)
    parser.add_argument("--min-alpha", type=float, default=0.0)
    parser.add_argument("--max-p-value", type=float, default=0.20)
    parser.add_argument("--max-mdd", type=float, default=0.20)
    return parser.parse_args()


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


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _find_latest(base: Path, pattern: str) -> Path | None:
    paths = [p for p in base.glob(pattern) if p.is_file()]
    if not paths:
        return None
    return max(paths, key=lambda p: (p.stat().st_mtime, str(p)))


def _parse_splits(raw: str) -> list[dict[str, str]]:
    splits: list[dict[str, str]] = []
    for idx, chunk in enumerate([part.strip() for part in raw.split(";") if part.strip()], start=1):
        name = f"split_{idx:02d}"
        body = chunk
        if ":" in chunk:
            maybe_name, body = chunk.split(":", 1)
            if maybe_name.strip():
                name = maybe_name.strip()
        parts = [part.strip() for part in body.split(",")]
        if len(parts) != 4:
            raise ValueError(f"Invalid rolling split {chunk!r}; expected 4 dates")
        splits.append({
            "name": name,
            "train_start": parts[0],
            "train_end": parts[1],
            "test_start": parts[2],
            "test_end": parts[3],
        })
    if not splits:
        raise ValueError("No rolling splits supplied")
    return splits


def _best_oos_rule(oos_csv: Path) -> dict[str, str]:
    rows = _read_csv_rows(oos_csv)
    if not rows:
        raise ValueError(f"OOS CSV is empty: {oos_csv}")
    return sorted(
        rows,
        key=lambda row: (
            _float(row.get("oos_rank"), math.inf),
            _float(row.get("portfolio_p_value"), math.inf),
            -_float(row.get("portfolio_alpha"), -math.inf),
        ),
    )[0]


def _metrics_from_row(row: dict[str, str]) -> dict[str, Any]:
    return {
        "sample_size": _int(row.get("n_slots")),
        "active_trades": _int(row.get("n_active_trades")),
        "portfolio_return_by_date_mean": _float(row.get("portfolio_return_by_date_mean")),
        "alpha": _float(row.get("portfolio_alpha")),
        "p_value": _float(row.get("portfolio_p_value")),
        "mdd": _float(row.get("portfolio_mdd")),
        "n_test_dates": _int(row.get("n_test_dates")),
        "rule_name": row.get("rule_name"),
        "take_profit": _float(row.get("take_profit")),
        "stop_loss": _float(row.get("stop_loss")),
        "hold_days": _int(row.get("hold_days")),
    }


def _evaluate_metrics(metrics: dict[str, Any], args: argparse.Namespace) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if _int(metrics.get("sample_size")) < args.min_sample_size:
        reasons.append(f"sample_size {metrics.get('sample_size')} < {args.min_sample_size}")
    if _int(metrics.get("active_trades")) < args.min_active_trades:
        reasons.append(f"active_trades {metrics.get('active_trades')} < {args.min_active_trades}")
    if _float(metrics.get("alpha"), -math.inf) < args.min_alpha:
        reasons.append(f"alpha {metrics.get('alpha')} < {args.min_alpha}")
    if _float(metrics.get("p_value"), math.inf) > args.max_p_value:
        reasons.append(f"p_value {metrics.get('p_value')} > {args.max_p_value}")
    if _float(metrics.get("mdd"), math.inf) > args.max_mdd:
        reasons.append(f"mdd {metrics.get('mdd')} > {args.max_mdd}")
    return (not reasons, reasons)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def _run_split(split: dict[str, str], split_dir: Path, args: argparse.Namespace, candidate_dir: Path) -> dict[str, Any]:
    reports_dir = split_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        args.python_bin,
        args.benchmark_script,
        "--config", args.config,
        "--train-test",
        "--train-start", split["train_start"],
        "--train-end", split["train_end"],
        "--test-start", split["test_start"],
        "--test-end", split["test_end"],
        "--top-n", str(args.top_n),
        "--period", args.period,
        "--frequency", args.frequency,
        "--random-baseline", str(args.random_baseline),
        "--bootstrap", str(args.bootstrap),
        "--train-top-k-rules", str(args.train_top_k_rules),
        "--rank-mode", args.rank_mode,
        "--xgb-blend-weight", args.xgb_blend_weight,
        "--embargo-trading-days", str(args.embargo_trading_days),
        "--trade-sim",
        "--min-dollar-volume", str(args.min_dollar_volume),
        "--min-price", str(args.min_price),
        "--max-gap-open", str(args.max_gap_open),
        "--entry-penalty-bps", str(args.entry_penalty_bps),
    ]
    if args.max_dates is not None:
        cmd.extend(["--max-dates", str(args.max_dates)])

    log_path = split_dir / "benchmark.log"
    env = os.environ.copy()
    env["PHX_MODELS_DIR"] = str(candidate_dir)
    env["PHX_REPORTS_DIR"] = str(reports_dir)
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, text=True, env=env, check=False)

    result: dict[str, Any] = {
        "name": split["name"],
        "train_start": split["train_start"],
        "train_end": split["train_end"],
        "test_start": split["test_start"],
        "test_end": split["test_end"],
        "report_root": str(reports_dir),
        "log_path": str(log_path),
        "returncode": proc.returncode,
        "passed": False,
        "reasons": [],
    }
    if proc.returncode != 0:
        result["reasons"] = [f"benchmark exited with {proc.returncode}"]
        return result

    oos_csv = _find_latest(reports_dir, "benchmark_train_test_*/benchmark_oos_rules.csv")
    summary_csv = _find_latest(reports_dir, "benchmark_train_test_*/benchmark_train_test_summary.csv")
    if oos_csv is None:
        result["reasons"] = ["benchmark_oos_rules.csv not found"]
        return result
    best = _best_oos_rule(oos_csv)
    metrics = _metrics_from_row(best)
    passed, reasons = _evaluate_metrics(metrics, args)
    result.update({
        "passed": passed,
        "reasons": reasons,
        "metrics": metrics,
        "oos_rules_csv": str(oos_csv),
        "summary_csv": str(summary_csv) if summary_csv else None,
    })
    return result


def main() -> int:
    args = _parse_args()
    candidate_dir = Path(args.candidate_dir).resolve()
    output_json = Path(args.output_json).resolve() if args.output_json else candidate_dir / "rolling_oos_summary.json"
    try:
        splits = _parse_splits(args.splits)
        root = candidate_dir / "rolling_oos"
        root.mkdir(parents=True, exist_ok=True)
        split_results = []
        for idx, split in enumerate(splits, start=1):
            split_dir = root / f"{idx:02d}_{split['name']}"
            print(f"ROLLING_SPLIT_START {split['name']} {split['train_start']}..{split['train_end']} -> {split['test_start']}..{split['test_end']}")
            result = _run_split(split, split_dir, args, candidate_dir)
            split_results.append(result)
            status = "PASSED" if result.get("passed") else "FAILED"
            print(f"ROLLING_SPLIT_{status} {split['name']}")
            for reason in result.get("reasons", []):
                print(f"- {reason}")

        pass_count = sum(1 for result in split_results if result.get("passed"))
        n_splits = len(split_results)
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "candidate_dir": str(candidate_dir),
            "passed": pass_count == n_splits,
            "summary": {
                "n_splits": n_splits,
                "pass_count": pass_count,
                "pass_rate": pass_count / n_splits if n_splits else 0.0,
                "min_sample_size": args.min_sample_size,
                "min_active_trades": args.min_active_trades,
                "min_alpha": args.min_alpha,
                "max_p_value": args.max_p_value,
                "max_mdd": args.max_mdd,
            },
            "splits": split_results,
        }
        _write_json(output_json, payload)
        print(f"ROLLING_OOS_SUMMARY {output_json}")
        return OK if payload["passed"] else FAILED
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return ERROR


if __name__ == "__main__":
    raise SystemExit(main())
