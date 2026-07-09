#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


CATEGORY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("p_value", re.compile(r"\bp[_ -]?value\b", re.I)),
    ("rolling_oos", re.compile(r"rolling OOS|rolling", re.I)),
    ("leakage_audit", re.compile(r"leakage|audit", re.I)),
    ("xgb_or_rank_mode", re.compile(r"xgb|rank_mode|rank mode|ranking", re.I)),
    ("mdd", re.compile(r"\bmdd\b", re.I)),
    ("alpha", re.compile(r"\balpha\b", re.I)),
    ("portfolio_return", re.compile(r"portfolio_return|portfolio", re.I)),
    ("sample_size", re.compile(r"sample_size|sample", re.I)),
    ("active_trades", re.compile(r"active_trades|active", re.I)),
    ("current_missing", re.compile(r"current metrics missing|initial promotion", re.I)),
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze Phoenix Quant candidate gate failure reasons.")
    parser.add_argument("--models-root", default="models")
    parser.add_argument("--limit", type=int, default=50)
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


def _categorize(reason: str) -> str:
    for category, pattern in CATEGORY_PATTERNS:
        if pattern.search(reason):
            return category
    return "other"


def _candidate_record(path: Path) -> dict[str, Any]:
    payload = _load_json(path / "metrics.json")
    leakage = _load_json(path / "leakage_audit.json")
    rolling = _load_json(path / "rolling_oos_summary.json")
    if payload is None:
        status = "pending"
        reasons: list[str] = []
        metrics: dict[str, Any] = {}
        promoted = False
        if leakage and leakage.get("passed") is False:
            status = "audit_failed"
            reasons = [
                f"leakage audit failed: {check.get('name')}"
                for check in leakage.get("checks", [])
                if not check.get("passed")
            ]
    else:
        gate = payload.get("gate", {}) if isinstance(payload.get("gate"), dict) else {}
        reasons = [str(r) for r in gate.get("reasons", [])]
        promoted = bool(gate.get("promoted"))
        status = "promoted" if promoted else "rejected"
        metrics = payload.get("metrics", {}) if isinstance(payload.get("metrics"), dict) else {}

    categories = [_categorize(reason) for reason in reasons]
    rolling_summary = rolling.get("summary", {}) if isinstance(rolling, dict) and isinstance(rolling.get("summary"), dict) else {}
    return {
        "name": path.name,
        "path": str(path),
        "status": status,
        "promoted": promoted,
        "reasons": reasons,
        "categories": categories,
        "metrics": metrics,
        "leakage_passed": leakage.get("passed") if isinstance(leakage, dict) else None,
        "rolling_passed": rolling.get("passed") if isinstance(rolling, dict) else None,
        "rolling_pass_rate": rolling_summary.get("pass_rate"),
    }


def _build_analysis(args: argparse.Namespace) -> dict[str, Any]:
    models_root = Path(args.models_root)
    candidates = [_candidate_record(path) for path in _candidate_dirs(models_root / "candidates", args.limit)]
    status_counts = Counter(c["status"] for c in candidates)
    category_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for candidate in candidates:
        for reason in candidate["reasons"]:
            reason_counts[reason] += 1
        seen_categories = set(candidate["categories"])
        for category in seen_categories:
            category_counts[category] += 1
            by_category[category].append({
                "name": candidate["name"],
                "status": candidate["status"],
                "reasons": [r for r in candidate["reasons"] if _categorize(r) == category],
            })

    next_actions = _recommend_actions(category_counts)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "models_root": str(models_root),
        "n_candidates": len(candidates),
        "status_counts": dict(status_counts.most_common()),
        "category_counts": dict(category_counts.most_common()),
        "reason_counts": dict(reason_counts.most_common()),
        "next_actions": next_actions,
        "candidates": candidates,
        "by_category": dict(by_category),
    }


def _recommend_actions(category_counts: Counter[str]) -> list[str]:
    if not category_counts:
        return ["Wait for at least one auto-cycle candidate, then rerun failure analysis."]
    actions: list[str] = []
    top_category = category_counts.most_common(1)[0][0]
    if top_category == "p_value":
        actions.append("Do not loosen p-value first. Inspect random baseline and rolling split consistency before adding features.")
    elif top_category == "rolling_oos":
        actions.append("Compare failed rolling split windows; look for regime-specific weakness before changing production defaults.")
    elif top_category == "leakage_audit":
        actions.append("Fix validation window/report integrity before trusting any candidate metrics.")
    elif top_category == "xgb_or_rank_mode":
        actions.append("Keep XGB/ranking promotion disabled unless separate rolling OOS proves improvement.")
    elif top_category == "mdd":
        actions.append("Investigate risk filters: gap, liquidity, sector stress, and stop-loss/hold-day settings.")
    elif top_category == "alpha":
        actions.append("Study why candidate alpha is weak versus random baseline before increasing model complexity.")
    elif top_category == "active_trades":
        actions.append("Check whether filters are too restrictive or test windows too sparse; do not promote low-sample candidates.")
    else:
        actions.append("Review raw gate reasons and group repeated failures into a feature, filter, or stricter validation rule.")
    actions.append("Use operator feedback categories to confirm whether the same failure mode is visible in Telegram candidates.")
    return actions


def _print_analysis(analysis: dict[str, Any]) -> None:
    print("Phoenix Candidate Failure Analysis")
    print("==================================")
    print(f"candidates analyzed: {analysis['n_candidates']}")
    if analysis["n_candidates"] == 0:
        print("No candidate metrics yet. Wait for the first auto-cycle run, then rerun this script.")
        return

    print("\nStatus counts:")
    for status, count in analysis["status_counts"].items():
        print(f"- {status}: {count}")

    print("\nFailure categories:")
    if not analysis["category_counts"]:
        print("- no failure reasons recorded")
    for category, count in analysis["category_counts"].items():
        print(f"- {category}: {count}")

    print("\nTop raw reasons:")
    if not analysis["reason_counts"]:
        print("- no raw reasons recorded")
    for reason, count in list(analysis["reason_counts"].items())[:10]:
        print(f"- ({count}) {reason}")

    print("\nRecent candidates:")
    for candidate in analysis["candidates"][:10]:
        metrics = candidate.get("metrics", {}) if isinstance(candidate.get("metrics"), dict) else {}
        print(
            f"- {candidate['name']} [{candidate['status']}] "
            f"portfolio={_fmt_pct(metrics.get('portfolio_return_by_date_mean'))} "
            f"alpha={_fmt_pct(metrics.get('alpha'))} "
            f"p={_fmt_num(metrics.get('p_value'))} "
            f"mdd={_fmt_pct(metrics.get('mdd'))} "
            f"audit={candidate.get('leakage_passed')} rolling={candidate.get('rolling_passed')}"
        )
        for reason in candidate.get("reasons", [])[:2]:
            print(f"  reason: {reason}")
        if len(candidate.get("reasons", [])) > 2:
            print(f"  ... {len(candidate['reasons']) - 2} more reasons")

    print("\nRecommended next actions:")
    for action in analysis["next_actions"]:
        print(f"- {action}")


def main() -> int:
    args = _parse_args()
    analysis = _build_analysis(args)
    if args.json:
        print(json.dumps(analysis, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_analysis(analysis)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
