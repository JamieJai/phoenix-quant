#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize Phoenix Quant auto-learning state.")
    parser.add_argument("--models-root", default="models")
    parser.add_argument("--log-file", default="logs/phoenix_auto_validation.log")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--log-lines", type=int, default=40)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else None


def _float(value: Any, default: float = math.nan) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _fmt_pct(value: Any) -> str:
    val = _float(value)
    return "n/a" if not math.isfinite(val) else f"{val * 100:.2f}%"


def _fmt_num(value: Any, digits: int = 4) -> str:
    val = _float(value)
    return "n/a" if not math.isfinite(val) else f"{val:.{digits}f}"


def _candidate_dirs(candidates_root: Path, limit: int) -> list[Path]:
    if not candidates_root.exists():
        return []
    dirs = [p for p in candidates_root.iterdir() if p.is_dir()]
    return sorted(dirs, key=lambda p: (p.name, p.stat().st_mtime), reverse=True)[:limit]


def _candidate_summary(path: Path) -> dict[str, Any]:
    metrics_payload = _load_json(path / "metrics.json")
    leakage = _load_json(path / "leakage_audit.json")
    rolling = _load_json(path / "rolling_oos_summary.json")
    top_candidates = path / "top_candidates.txt"

    status = "pending"
    reasons: list[str] = []
    metrics: dict[str, Any] = {}
    gate: dict[str, Any] = {}
    if metrics_payload:
        metrics = metrics_payload.get("metrics", {}) if isinstance(metrics_payload.get("metrics"), dict) else {}
        gate = metrics_payload.get("gate", {}) if isinstance(metrics_payload.get("gate"), dict) else {}
        if gate.get("promoted"):
            status = "promoted"
        else:
            status = "rejected"
        reasons = [str(r) for r in gate.get("reasons", [])]
    elif leakage and leakage.get("passed") is False:
        status = "audit_failed"

    rolling_summary = rolling.get("summary", {}) if isinstance(rolling, dict) and isinstance(rolling.get("summary"), dict) else {}
    return {
        "name": path.name,
        "path": str(path),
        "status": status,
        "promoted": bool(gate.get("promoted")) if gate else False,
        "reasons": reasons,
        "metrics": metrics,
        "promotion_rank_mode": metrics_payload.get("promotion_rank_mode") if metrics_payload else None,
        "promotion_xgb_blend_weight": metrics_payload.get("promotion_xgb_blend_weight") if metrics_payload else None,
        "leakage_passed": leakage.get("passed") if isinstance(leakage, dict) else None,
        "rolling_passed": rolling.get("passed") if isinstance(rolling, dict) else None,
        "rolling_n_splits": rolling_summary.get("n_splits"),
        "rolling_pass_rate": rolling_summary.get("pass_rate"),
        "has_top_candidates": top_candidates.exists(),
    }


def _read_log_tail(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        content = f.readlines()
    return [line.rstrip("\n") for line in content[-lines:]]


def _build_status(args: argparse.Namespace) -> dict[str, Any]:
    models_root = Path(args.models_root)
    current_payload = _load_json(models_root / "current" / "metrics.json")
    candidates = [_candidate_summary(path) for path in _candidate_dirs(models_root / "candidates", args.limit)]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "models_root": str(models_root),
        "current": current_payload,
        "candidates": candidates,
        "log_file": args.log_file,
        "log_tail": _read_log_tail(Path(args.log_file), args.log_lines),
    }


def _print_status(status: dict[str, Any]) -> None:
    print("Phoenix Auto Learning Status")
    print("============================")
    current = status.get("current")
    if current:
        metrics = current.get("metrics", {}) if isinstance(current.get("metrics"), dict) else {}
        print("Current model:")
        print(f"- candidate: {current.get('candidate_dir', 'n/a')}")
        print(f"- rank_mode: {current.get('promotion_rank_mode', 'n/a')} / xgb={current.get('promotion_xgb_blend_weight', 'n/a')}")
        print(f"- portfolio_mean: {_fmt_pct(metrics.get('portfolio_return_by_date_mean'))}")
        print(f"- alpha: {_fmt_pct(metrics.get('alpha'))} / p={_fmt_num(metrics.get('p_value'))} / mdd={_fmt_pct(metrics.get('mdd'))}")
        print(f"- active_trades: {metrics.get('active_trades', 'n/a')} / sample_size: {metrics.get('sample_size', 'n/a')}")
    else:
        print("Current model: not promoted yet")

    print()
    candidates = status.get("candidates", [])
    print(f"Recent candidates: {len(candidates)}")
    if not candidates:
        print("- no candidate artifacts yet")
    for c in candidates:
        metrics = c.get("metrics", {}) if isinstance(c.get("metrics"), dict) else {}
        print(f"- {c['name']} [{c['status']}]")
        print(
            "  "
            f"portfolio={_fmt_pct(metrics.get('portfolio_return_by_date_mean'))} "
            f"alpha={_fmt_pct(metrics.get('alpha'))} "
            f"p={_fmt_num(metrics.get('p_value'))} "
            f"mdd={_fmt_pct(metrics.get('mdd'))} "
            f"active={metrics.get('active_trades', 'n/a')} "
            f"sample={metrics.get('sample_size', 'n/a')}"
        )
        print(
            "  "
            f"audit={c.get('leakage_passed')} rolling={c.get('rolling_passed')} "
            f"rolling_pass_rate={_fmt_num(c.get('rolling_pass_rate'), 2)} "
            f"rank_mode={c.get('promotion_rank_mode')} xgb={c.get('promotion_xgb_blend_weight')}"
        )
        for reason in c.get("reasons", [])[:3]:
            print(f"  reason: {reason}")
        if len(c.get("reasons", [])) > 3:
            print(f"  ... {len(c['reasons']) - 3} more reasons")

    print()
    print(f"Log tail: {status.get('log_file')}")
    tail = status.get("log_tail", [])
    if not tail:
        print("- no log file yet")
    else:
        for line in tail[-10:]:
            print(line)


def main() -> int:
    args = _parse_args()
    status = _build_status(args)
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_status(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
