#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROMOTED = 0
ERROR = 1
REJECTED = 2


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gate Phoenix Quant candidate validation metrics before promotion."
    )
    parser.add_argument("--candidate-dir", required=True)
    parser.add_argument("--current-dir", default="models/current")
    parser.add_argument("--archive-root", default="models/archive")
    parser.add_argument("--oos-rules-csv", default=None)
    parser.add_argument("--summary-csv", default=None)
    parser.add_argument("--min-sample-size", type=int, default=50)
    parser.add_argument("--min-portfolio-delta", type=float, default=0.001)
    parser.add_argument("--max-p-value", type=float, default=0.20)
    parser.add_argument("--max-mdd-slippage", type=float, default=0.02)
    parser.add_argument("--min-active-trades", type=int, default=30)
    parser.add_argument("--allow-initial-promotion", action="store_true")
    parser.add_argument("--allow-xgb-promotion", action="store_true")
    parser.add_argument("--leakage-audit-json", default=None)
    parser.add_argument("--require-leakage-audit", action="store_true")
    parser.add_argument("--rolling-summary-json", default=None)
    parser.add_argument("--require-rolling-oos", action="store_true")
    parser.add_argument("--min-rolling-splits", type=int, default=2)
    parser.add_argument("--min-rolling-pass-rate", type=float, default=1.0)
    return parser.parse_args()


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _find_latest(base: Path, pattern: str) -> Path | None:
    paths = [p for p in base.glob(pattern) if p.is_file()]
    if not paths:
        return None
    return max(paths, key=lambda p: (p.stat().st_mtime, str(p)))


def _float(value: Any, default: float = math.nan) -> float:
    if value is None or value == "":
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _int(value: Any, default: int = 0) -> int:
    val = _float(value, math.nan)
    return int(val) if math.isfinite(val) else default


def _parse_float_list(value: Any) -> list[float]:
    if value is None:
        return []
    out: list[float] = []
    for part in str(value).split(","):
        parsed = _float(part.strip(), math.nan)
        if math.isfinite(parsed):
            out.append(parsed)
    return out


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _best_oos_rule(rows: list[dict[str, str]]) -> dict[str, str]:
    if not rows:
        raise ValueError("OOS rules CSV is empty.")

    def rank_key(row: dict[str, str]) -> tuple[float, float, float]:
        oos_rank = _float(row.get("oos_rank"), math.inf)
        p_value = _float(row.get("portfolio_p_value"), math.inf)
        alpha = _float(row.get("portfolio_alpha"), -math.inf)
        return (oos_rank, p_value, -alpha)

    return sorted(rows, key=rank_key)[0]


def _load_current_metrics(current_dir: Path) -> dict[str, Any] | None:
    metrics_path = current_dir / "metrics.json"
    if not metrics_path.exists():
        return None
    with metrics_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    metrics = data.get("metrics", data)
    if not isinstance(metrics, dict):
        raise ValueError(f"Invalid current metrics format: {metrics_path}")
    return metrics


def _load_json_if_exists(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return data


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(_json_safe(payload), f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def _copy_if_exists(src: Path | None, dst: Path) -> None:
    if src is None or not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _resolve_report_path(value: Any) -> Path | None:
    if value is None or value == "":
        return None
    path = Path(str(value))
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _find_rank_mode_comparison(candidate_dir: Path, summary: dict[str, str]) -> Path | None:
    test_report_dir = _resolve_report_path(summary.get("test_report_dir"))
    if test_report_dir is not None:
        candidate = test_report_dir / "benchmark_rank_mode_comparison.csv"
        if candidate.exists():
            return candidate

    paths = [
        p for p in candidate_dir.glob("reports/benchmark_*/benchmark_rank_mode_comparison.csv")
        if p.is_file()
    ]
    if not paths:
        return None
    return max(paths, key=lambda p: (p.stat().st_mtime, str(p)))


def _has_entries(path: Path) -> bool:
    return path.exists() and any(path.iterdir())


def _promote(candidate_dir: Path, current_dir: Path, archive_root: Path, stamp: str) -> Path | None:
    archive_dir: Path | None = None
    tmp_current = current_dir.with_name(f"{current_dir.name}.tmp.{stamp}")
    if tmp_current.exists():
        shutil.rmtree(tmp_current)
    shutil.copytree(candidate_dir, tmp_current)

    if _has_entries(current_dir):
        archive_root.mkdir(parents=True, exist_ok=True)
        archive_dir = archive_root / stamp
        suffix = 1
        while archive_dir.exists():
            archive_dir = archive_root / f"{stamp}_{suffix}"
            suffix += 1
        shutil.copytree(current_dir, archive_dir)

    if current_dir.exists():
        shutil.rmtree(current_dir)
    current_dir.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp_current, current_dir)
    return archive_dir


def _promotion_rank_metadata(summary: dict[str, str]) -> dict[str, Any]:
    rank_mode = str(summary.get("rank_mode") or "unknown").strip() or "unknown"
    weights = _parse_float_list(summary.get("xgb_blend_weights"))
    if rank_mode == "decision":
        promotion_weight = 0.0
    elif weights:
        promotion_weight = 0.30 if any(abs(w - 0.30) < 1e-12 for w in weights) else weights[0]
    else:
        promotion_weight = math.nan
    return {
        "promotion_rank_mode": rank_mode,
        "promotion_xgb_blend_weight": promotion_weight,
        "benchmark_xgb_blend_weights": weights,
    }


def _build_metrics(candidate_dir: Path, oos_csv: Path, summary_csv: Path | None) -> dict[str, Any]:
    rows = _read_csv_rows(oos_csv)
    best = _best_oos_rule(rows)

    sample_size = _int(best.get("n_slots"))
    active_trades = _int(best.get("n_active_trades"))
    metrics = {
        "sample_size": sample_size,
        "portfolio_return_by_date_mean": _float(best.get("portfolio_return_by_date_mean")),
        "alpha": _float(best.get("portfolio_alpha")),
        "p_value": _float(best.get("portfolio_p_value")),
        "mdd": _float(best.get("portfolio_mdd")),
        "active_trades": active_trades,
        "n_test_dates": _int(best.get("n_test_dates")),
        "cash_weight_mean": _float(best.get("cash_weight_mean")),
    }
    selected_rule = {
        "rule_name": best.get("rule_name"),
        "rule_source": best.get("rule_source"),
        "take_profit": _float(best.get("take_profit")),
        "stop_loss": _float(best.get("stop_loss")),
        "hold_days": _int(best.get("hold_days")),
        "oos_rank": _int(best.get("oos_rank")),
    }
    summary_rows = _read_csv_rows(summary_csv) if summary_csv and summary_csv.exists() else []
    summary = summary_rows[0] if summary_rows else {}
    rank_metadata = _promotion_rank_metadata(summary)
    benchmark_report_dir = str(summary_csv.parent) if summary_csv else str(oos_csv.parent)
    generated_at = datetime.now().isoformat(timespec="seconds")
    payload = {
        "generated_at": generated_at,
        "candidate_dir": str(candidate_dir),
        "benchmark_report_dir": benchmark_report_dir,
        "metrics": metrics,
        "selected_rule": selected_rule,
        **rank_metadata,
        "source_files": {
            "oos_rules_csv": str(oos_csv),
            "summary_csv": str(summary_csv) if summary_csv else None,
            "rank_mode_comparison_csv": None,
        },
        "validation_summary": summary,
    }
    rank_mode_comparison_csv = _find_rank_mode_comparison(candidate_dir, summary)
    if rank_mode_comparison_csv is not None:
        payload["source_files"]["rank_mode_comparison_csv"] = str(rank_mode_comparison_csv)
    return payload


def _finite_metric(metrics: dict[str, Any], key: str) -> float:
    value = _float(metrics.get(key), math.nan)
    if not math.isfinite(value):
        raise ValueError(f"Metric {key} is missing or not finite.")
    return value


def _evaluate(
    candidate: dict[str, Any],
    current: dict[str, Any] | None,
    args: argparse.Namespace,
    leakage_audit: dict[str, Any] | None,
    rolling_oos: dict[str, Any] | None,
) -> tuple[bool, list[str]]:
    c = candidate["metrics"]
    reasons: list[str] = []

    c_sample = _int(c.get("sample_size"))
    c_portfolio = _finite_metric(c, "portfolio_return_by_date_mean")
    c_alpha = _finite_metric(c, "alpha")
    c_p_value = _finite_metric(c, "p_value")
    c_mdd = _finite_metric(c, "mdd")
    c_active = _int(c.get("active_trades"))

    if c_sample < args.min_sample_size:
        reasons.append(f"sample_size {c_sample} < {args.min_sample_size}")
    if c_active < args.min_active_trades:
        reasons.append(f"active_trades {c_active} < {args.min_active_trades}")

    promotion_rank_mode = str(candidate.get("promotion_rank_mode") or "unknown")
    promotion_xgb_weight = _float(candidate.get("promotion_xgb_blend_weight"), math.nan)
    if args.require_leakage_audit:
        if leakage_audit is None:
            reasons.append("leakage audit is required but missing")
        elif not leakage_audit.get("passed"):
            failed_checks = [str(c.get("name")) for c in leakage_audit.get("checks", []) if not c.get("passed")]
            detail = ", ".join(failed_checks) if failed_checks else "unknown"
            reasons.append(f"leakage audit failed: {detail}")

    if args.require_rolling_oos:
        if rolling_oos is None:
            reasons.append("rolling OOS is required but missing")
        else:
            summary = rolling_oos.get("summary", {}) if isinstance(rolling_oos.get("summary"), dict) else {}
            n_splits = _int(summary.get("n_splits"))
            pass_rate = _float(summary.get("pass_rate"), math.nan)
            if n_splits < args.min_rolling_splits:
                reasons.append(f"rolling OOS splits {n_splits} < {args.min_rolling_splits}")
            if not math.isfinite(pass_rate) or pass_rate < args.min_rolling_pass_rate:
                reasons.append(f"rolling OOS pass_rate {pass_rate} < {args.min_rolling_pass_rate}")
            if not rolling_oos.get("passed"):
                failed = [str(s.get("name")) for s in rolling_oos.get("splits", []) if not s.get("passed")]
                if failed:
                    reasons.append(f"rolling OOS failed splits: {', '.join(failed)}")

    if not args.allow_xgb_promotion:
        if promotion_rank_mode != "decision":
            reasons.append(
                f"rank_mode {promotion_rank_mode} is not eligible for promotion "
                "without --allow-xgb-promotion"
            )
        if math.isfinite(promotion_xgb_weight) and promotion_xgb_weight > 0.0:
            reasons.append(
                f"xgb_blend_weight {promotion_xgb_weight:.12g} > 0 without --allow-xgb-promotion"
            )

    if current is None:
        if not args.allow_initial_promotion:
            reasons.append("current metrics missing and initial promotion is disabled")
        if c_alpha < 0.0:
            reasons.append(f"alpha {c_alpha:.12g} < initial baseline 0")
        if c_p_value > args.max_p_value:
            reasons.append(f"p_value {c_p_value:.12g} > {args.max_p_value}")
        return (not reasons, reasons)

    current_portfolio = _finite_metric(current, "portfolio_return_by_date_mean")
    current_alpha = _finite_metric(current, "alpha")
    current_p_value = _finite_metric(current, "p_value")
    current_mdd = _finite_metric(current, "mdd")

    required_portfolio = current_portfolio + args.min_portfolio_delta
    if c_portfolio < required_portfolio:
        reasons.append(
            "portfolio_return_by_date_mean "
            f"{c_portfolio:.12g} < current {current_portfolio:.12g} + {args.min_portfolio_delta}"
        )
    if c_alpha < current_alpha:
        reasons.append(f"alpha {c_alpha:.12g} < current {current_alpha:.12g}")
    if not (c_p_value <= current_p_value or c_p_value <= args.max_p_value):
        reasons.append(
            f"p_value {c_p_value:.12g} > current {current_p_value:.12g} "
            f"and > {args.max_p_value}"
        )
    if c_mdd > current_mdd + args.max_mdd_slippage:
        reasons.append(
            f"mdd {c_mdd:.12g} > current {current_mdd:.12g} + {args.max_mdd_slippage}"
        )
    return (not reasons, reasons)


def main() -> int:
    args = _parse_args()
    candidate_dir = Path(args.candidate_dir).resolve()
    current_dir = Path(args.current_dir).resolve()
    archive_root = Path(args.archive_root).resolve()

    try:
        if not candidate_dir.exists():
            raise FileNotFoundError(f"Candidate dir not found: {candidate_dir}")
        oos_csv = Path(args.oos_rules_csv).resolve() if args.oos_rules_csv else _find_latest(
            candidate_dir, "reports/benchmark_train_test_*/benchmark_oos_rules.csv"
        )
        if oos_csv is None:
            raise FileNotFoundError(f"benchmark_oos_rules.csv not found under {candidate_dir}")
        summary_csv = Path(args.summary_csv).resolve() if args.summary_csv else _find_latest(
            candidate_dir, "reports/benchmark_train_test_*/benchmark_train_test_summary.csv"
        )

        payload = _build_metrics(candidate_dir, oos_csv, summary_csv)
        leakage_path = Path(args.leakage_audit_json).resolve() if args.leakage_audit_json else candidate_dir / "leakage_audit.json"
        rolling_path = Path(args.rolling_summary_json).resolve() if args.rolling_summary_json else candidate_dir / "rolling_oos_summary.json"
        leakage_audit = _load_json_if_exists(leakage_path)
        rolling_oos = _load_json_if_exists(rolling_path)
        payload["leakage_audit"] = leakage_audit
        payload["rolling_oos"] = rolling_oos
        _copy_if_exists(oos_csv, candidate_dir / "rules" / "benchmark_oos_rules.csv")
        _copy_if_exists(summary_csv, candidate_dir / "rules" / "benchmark_train_test_summary.csv")
        rank_comparison_csv = Path(payload["source_files"]["rank_mode_comparison_csv"]).resolve() if payload["source_files"].get("rank_mode_comparison_csv") else None
        _copy_if_exists(rank_comparison_csv, candidate_dir / "rules" / "benchmark_rank_mode_comparison.csv")
        _write_json(candidate_dir / "rules" / "selected_rule.json", payload["selected_rule"])

        current_metrics = _load_current_metrics(current_dir)
        promoted, reasons = _evaluate(payload, current_metrics, args, leakage_audit, rolling_oos)
        payload["gate"] = {
            "promoted": promoted,
            "reasons": reasons,
            "thresholds": {
                "min_sample_size": args.min_sample_size,
                "min_portfolio_delta": args.min_portfolio_delta,
                "max_p_value": args.max_p_value,
                "max_mdd_slippage": args.max_mdd_slippage,
                "min_active_trades": args.min_active_trades,
                "allow_initial_promotion": args.allow_initial_promotion,
                "allow_xgb_promotion": args.allow_xgb_promotion,
                "require_leakage_audit": args.require_leakage_audit,
                "require_rolling_oos": args.require_rolling_oos,
                "min_rolling_splits": args.min_rolling_splits,
                "min_rolling_pass_rate": args.min_rolling_pass_rate,
            },
            "current_metrics": current_metrics,
        }

        if promoted:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            payload["promotion"] = {
                "promoted_at": datetime.now().isoformat(timespec="seconds"),
                "archive_dir": None,
            }
            _write_json(candidate_dir / "metrics.json", payload)
            archive_dir = _promote(candidate_dir, current_dir, archive_root, stamp)
            payload["promotion"]["archive_dir"] = str(archive_dir) if archive_dir else None
            _write_json(current_dir / "metrics.json", payload)
            _write_json(current_dir / "promotion.json", payload["promotion"])
            print(f"PROMOTED candidate={candidate_dir} current={current_dir}")
            if archive_dir:
                print(f"ARCHIVED previous_current={archive_dir}")
            return PROMOTED

        _write_json(candidate_dir / "metrics.json", payload)
        print(f"REJECTED candidate={candidate_dir}")
        for reason in reasons:
            print(f"- {reason}")
        return REJECTED
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return ERROR


if __name__ == "__main__":
    raise SystemExit(main())
