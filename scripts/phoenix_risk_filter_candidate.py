#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd


SKIP_ARGS: dict[str, dict[str, str]] = {
    "hard_skip_bear_only": {"adverse_regime_skip": "Bear Trend"},
    "hard_skip_nlr_only": {"adverse_sector_skip": "NLR"},
    "hard_skip_xlk_only": {"adverse_sector_skip": "XLK"},
    "hard_skip_nlr_xlk": {"adverse_sector_skip": "NLR,XLK"},
    "hard_skip_bear_nlr_xlk": {
        "adverse_sector_skip": "NLR,XLK",
        "adverse_regime_skip": "Bear Trend",
    },
    "conditional_skip_nlr_xlk_ai_growth": {
        "adverse_conditional_sector_skip": "NLR,XLK",
        "adverse_conditional_regime_skip": "AI Growth Rotation",
    },
    "conditional_skip_nlr_xlk_broad_bull": {
        "adverse_conditional_sector_skip": "NLR,XLK",
        "adverse_conditional_regime_skip": "Broad Bull",
    },
    "conditional_skip_nlr_xlk_bear": {
        "adverse_conditional_sector_skip": "NLR,XLK",
        "adverse_conditional_regime_skip": "Bear Trend",
    },
    "conditional_skip_nlr_xlk_risk_off": {
        "adverse_conditional_sector_skip": "NLR,XLK",
        "adverse_conditional_regime_skip": "Risk Off",
    },
    "conditional_skip_nlr_xlk_neutral_bear": {
        "adverse_conditional_sector_skip": "NLR,XLK",
        "adverse_conditional_regime_skip": "Neutral / Mixed,Bear Trend",
    },
    "conditional_skip_nlr_xlk_neutral_broad_bear": {
        "adverse_conditional_sector_skip": "NLR,XLK",
        "adverse_conditional_regime_skip": "Neutral / Mixed,Broad Bull,Bear Trend",
    },
    "conditional_skip_nlr_xlk_risk_bear": {
        "adverse_conditional_sector_skip": "NLR,XLK",
        "adverse_conditional_regime_skip": "Risk Off,Bear Trend",
    },
    "conditional_skip_nlr_xlk_risk_neutral_bear": {
        "adverse_conditional_sector_skip": "NLR,XLK",
        "adverse_conditional_regime_skip": "Risk Off,Neutral / Mixed,Bear Trend",
    },
    "conditional_skip_nlr_xlk_risk_neutral_broad_bear": {
        "adverse_conditional_sector_skip": "NLR,XLK",
        "adverse_conditional_regime_skip": "Risk Off,Neutral / Mixed,Broad Bull,Bear Trend",
    },
    "conditional_skip_nlr_xlk_low_score": {
        "adverse_conditional_sector_skip": "NLR,XLK",
        "adverse_conditional_max_rank_score": "82",
    },
    "conditional_skip_nlr_xlk_neutral_or_low_score": {
        "adverse_conditional_sector_skip": "NLR,XLK",
        "adverse_conditional_regime_skip": "Neutral / Mixed",
        "adverse_conditional_max_rank_score": "82",
    },
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pick benchmark-compatible adverse-risk filter candidates from an offline risk-filter experiment."
    )
    parser.add_argument("--summary-csv", required=True, help="risk_filter_experiment scenario_summary.csv")
    parser.add_argument("--layer", default="actual_top5_skip_to_cash")
    parser.add_argument("--min-active", type=int, default=200)
    parser.add_argument("--max-cash-weight", type=float, default=0.45)
    parser.add_argument("--max-mdd", type=float, default=0.20)
    parser.add_argument("--min-mean", type=float, default=0.0)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def _float(value: Any, default: float = math.nan) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _benchmark_args(row: pd.Series) -> list[str]:
    scenario = str(row["scenario"])
    args = SKIP_ARGS.get(scenario, {})
    out: list[str] = []
    for key in (
        "adverse_sector_skip",
        "adverse_regime_skip",
        "adverse_conditional_sector_skip",
        "adverse_conditional_regime_skip",
        "adverse_conditional_max_rank_score",
    ):
        value = args.get(key)
        if value:
            out.extend([f"--{key.replace('_', '-')}", value])
    return out


def _score(row: pd.Series) -> float:
    mean = _float(row.get("portfolio_return_by_date_mean"), 0.0)
    mdd = _float(row.get("portfolio_mdd"), 1.0)
    cash = _float(row.get("cash_weight"), 1.0)
    active = _float(row.get("n_active"), 0.0)
    return mean * 100.0 - mdd * 0.25 - cash * 0.05 + min(active, 300.0) / 100000.0


def main() -> int:
    args = _parse_args()
    path = Path(args.summary_csv)
    df = pd.read_csv(path, encoding="utf-8-sig")
    required = {"scenario", "layer", "n_active", "cash_weight", "portfolio_return_by_date_mean", "portfolio_mdd"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"missing columns in {path}: {sorted(missing)}")

    work = df[df["layer"].astype(str).eq(args.layer)].copy()
    work = work[work["scenario"].astype(str).isin(SKIP_ARGS)].copy()
    work["n_active"] = pd.to_numeric(work["n_active"], errors="coerce").fillna(0).astype(int)
    work["cash_weight"] = pd.to_numeric(work["cash_weight"], errors="coerce")
    work["portfolio_return_by_date_mean"] = pd.to_numeric(work["portfolio_return_by_date_mean"], errors="coerce")
    work["portfolio_mdd"] = pd.to_numeric(work["portfolio_mdd"], errors="coerce")
    work = work[
        work["n_active"].ge(args.min_active)
        & work["cash_weight"].le(args.max_cash_weight)
        & work["portfolio_mdd"].le(args.max_mdd)
        & work["portfolio_return_by_date_mean"].ge(args.min_mean)
    ].copy()
    work["candidate_score"] = work.apply(_score, axis=1)
    work = work.sort_values(
        ["candidate_score", "portfolio_return_by_date_mean", "portfolio_mdd", "cash_weight"],
        ascending=[False, False, True, True],
    ).head(max(1, int(args.top)))

    records: list[dict[str, Any]] = []
    for _, row in work.iterrows():
        records.append(
            {
                "scenario": str(row["scenario"]),
                "layer": str(row["layer"]),
                "n_active": int(row["n_active"]),
                "cash_weight": float(row["cash_weight"]),
                "portfolio_return_by_date_mean": float(row["portfolio_return_by_date_mean"]),
                "portfolio_mdd": float(row["portfolio_mdd"]),
                "candidate_score": float(row["candidate_score"]),
                "benchmark_args": _benchmark_args(row),
            }
        )

    if args.json:
        print(json.dumps({"summary_csv": str(path), "candidates": records}, ensure_ascii=False, indent=2))
        return 0

    print(f"summary_csv={path}")
    if not records:
        print("No benchmark-compatible candidates passed the screening thresholds.")
        return 2
    for idx, record in enumerate(records, start=1):
        print(
            f"#{idx} {record['scenario']} "
            f"mean={record['portfolio_return_by_date_mean']:.4%} "
            f"mdd={record['portfolio_mdd']:.2%} "
            f"cash={record['cash_weight']:.2%} "
            f"active={record['n_active']}"
        )
        print("  " + " ".join(record["benchmark_args"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
