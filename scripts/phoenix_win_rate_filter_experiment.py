#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


PRETRADE_NUMERIC_FEATURES = [
    "rank",
    "rank_score",
    "final_rank_score",
    "suitability_score",
    "confidence_score",
    "risk_score",
    "market_score",
    "sector_score",
    "sector_return_5d",
    "sector_return_20d",
    "regime_confidence",
    "pattern_rarity",
    "hold_score",
    "similarity_hit_5d",
    "similarity_hit_10d",
    "n_similar",
    "avg_similarity",
    "asof_dollar_volume",
    "gap_open_return",
]

CATEGORY_FEATURES = [
    "label",
    "regime",
    "sector_etf",
]


@dataclass(frozen=True)
class FilterSpec:
    name: str
    expression: str
    mask: pd.Series


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate pre-trade skip filters for improving active win rate without peeking into future outcomes."
    )
    parser.add_argument("--trade-csv", required=True, help="benchmark_trade_sim.csv path")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--max-cash-weight", type=float, default=0.60)
    parser.add_argument("--min-active-trades", type=int, default=120)
    parser.add_argument("--min-portfolio-mean", type=float, default=0.0)
    parser.add_argument("--max-mdd", type=float, default=0.15)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--quantiles", default="0.2,0.25,0.33,0.5,0.67,0.75,0.8")
    parser.add_argument("--min-filtered-active", type=int, default=8)
    return parser.parse_args()


def _read_trades(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"trade CSV not found: {path}")
    df = pd.read_csv(path, encoding="utf-8-sig")
    if df.empty:
        raise ValueError(f"trade CSV is empty: {path}")
    if "as_of" not in df.columns:
        raise ValueError("trade CSV must include as_of")
    df["as_of"] = pd.to_datetime(df["as_of"], errors="coerce").dt.date.astype(str)
    return df


def _bool_series(df: pd.DataFrame, column: str, default: bool = False) -> pd.Series:
    if column not in df.columns:
        return pd.Series(default, index=df.index)
    raw = df[column]
    if raw.dtype == bool:
        return raw.fillna(default)
    return raw.astype(str).str.lower().isin({"true", "1", "yes", "y"})


def _numeric_series(df: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if column not in df.columns:
        return pd.Series(default, index=df.index, dtype="float64")
    return pd.to_numeric(df[column], errors="coerce")


def _max_drawdown(returns: pd.Series) -> float:
    values = pd.to_numeric(returns, errors="coerce").fillna(0.0)
    if values.empty:
        return 0.0
    equity = (1.0 + values).cumprod()
    peak = equity.cummax()
    drawdown = (equity / peak) - 1.0
    return float(abs(drawdown.min()))


def _summarize(df: pd.DataFrame) -> dict[str, Any]:
    active = _bool_series(df, "is_active_trade", default=True)
    cash = _bool_series(df, "is_cash_slot", default=False)
    hit_tp = _bool_series(df, "hit_take_profit")
    hit_sl = _bool_series(df, "hit_stop_loss")
    slot_return = _numeric_series(df, "slot_return")
    trade_return = _numeric_series(df, "trade_return")
    active_returns = trade_return[active]
    by_date = slot_return.groupby(df["as_of"]).mean()

    return {
        "n_dates": int(df["as_of"].nunique()),
        "n_slots": int(len(df)),
        "n_active": int(active.sum()),
        "cash_slots": int(cash.sum()),
        "cash_weight": float(cash.mean()) if len(df) else 0.0,
        "active_win_rate": float((active_returns > 0).mean()) if len(active_returns) else 0.0,
        "active_avg_return": float(active_returns.mean()) if len(active_returns) else 0.0,
        "active_median_return": float(active_returns.median()) if len(active_returns) else 0.0,
        "portfolio_return_by_date_mean": float(by_date.mean()) if len(by_date) else 0.0,
        "portfolio_positive_date_rate": float((by_date > 0).mean()) if len(by_date) else 0.0,
        "portfolio_mdd": _max_drawdown(by_date),
        "take_profit_rate": float(hit_tp[active].mean()) if active.any() else 0.0,
        "stop_loss_rate": float(hit_sl[active].mean()) if active.any() else 0.0,
        "time_exit_rate": float((active & ~hit_tp & ~hit_sl).sum() / active.sum()) if active.any() else 0.0,
    }


def _apply_skip(df: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    out = df.copy()
    skip = mask.reindex(out.index, fill_value=False) & _bool_series(out, "is_active_trade", default=True)
    out.loc[skip, "slot_return"] = 0.0
    out.loc[skip, "trade_return"] = 0.0
    out.loc[skip, "net_return"] = 0.0
    out.loc[skip, "is_cash_slot"] = True
    out.loc[skip, "is_active_trade"] = False
    out.loc[skip, "filter_reason"] = "win_rate_filter"
    out.loc[skip, "exit_reason"] = "CASH"
    out.loc[skip, "hit_take_profit"] = False
    out.loc[skip, "hit_stop_loss"] = False
    return out


def _clean_name(value: Any) -> str:
    return str(value).strip().replace(" ", "_").replace("/", "_").replace(",", "_")


def _quantiles(raw: str) -> list[float]:
    values: list[float] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        value = float(item)
        if 0.0 < value < 1.0:
            values.append(value)
    return sorted(set(values))


def _category_specs(df: pd.DataFrame, min_filtered_active: int) -> Iterable[FilterSpec]:
    active = _bool_series(df, "is_active_trade", default=True)
    for feature in CATEGORY_FEATURES:
        if feature not in df.columns:
            continue
        values = df.loc[active, feature].dropna().astype(str)
        for value, count in values.value_counts().items():
            if count < min_filtered_active:
                continue
            mask = active & df[feature].astype(str).eq(value)
            yield FilterSpec(
                name=f"skip_{feature}_{_clean_name(value)}",
                expression=f"{feature} == {value}",
                mask=mask,
            )

    if {"sector_etf", "regime"}.issubset(df.columns):
        grouped = df.loc[active].groupby(["sector_etf", "regime"], dropna=True).size().sort_values(ascending=False)
        for (sector, regime), count in grouped.items():
            if count < min_filtered_active:
                continue
            mask = active & df["sector_etf"].astype(str).eq(str(sector)) & df["regime"].astype(str).eq(str(regime))
            yield FilterSpec(
                name=f"skip_sector_regime_{_clean_name(sector)}_{_clean_name(regime)}",
                expression=f"sector_etf == {sector} and regime == {regime}",
                mask=mask,
            )


def _numeric_specs(df: pd.DataFrame, qs: list[float], min_filtered_active: int) -> Iterable[FilterSpec]:
    active = _bool_series(df, "is_active_trade", default=True)
    for feature in PRETRADE_NUMERIC_FEATURES:
        if feature not in df.columns:
            continue
        values = pd.to_numeric(df.loc[active, feature], errors="coerce").dropna()
        if values.nunique() < 4:
            continue
        thresholds = values.quantile(qs).dropna().drop_duplicates()
        for q, threshold in thresholds.items():
            threshold = float(threshold)
            for op in ("le", "ge"):
                if op == "le":
                    mask = active & (pd.to_numeric(df[feature], errors="coerce") <= threshold)
                    expr = f"{feature} <= {threshold:.8g}"
                else:
                    mask = active & (pd.to_numeric(df[feature], errors="coerce") >= threshold)
                    expr = f"{feature} >= {threshold:.8g}"
                if int(mask.sum()) < min_filtered_active:
                    continue
                yield FilterSpec(
                    name=f"skip_{feature}_{op}_q{int(round(float(q) * 100)):02d}",
                    expression=expr,
                    mask=mask,
                )


def _targeted_combo_specs(df: pd.DataFrame, qs: list[float], min_filtered_active: int) -> Iterable[FilterSpec]:
    active = _bool_series(df, "is_active_trade", default=True)
    high_risk_features = [
        ("sector_return_5d", "ge"),
        ("gap_open_return", "ge"),
        ("market_score", "ge"),
        ("sector_return_20d", "ge"),
        ("risk_score", "le"),
        ("confidence_score", "le"),
        ("final_rank_score", "le"),
        ("hold_score", "le"),
    ]
    feature_masks: list[tuple[str, str, float, pd.Series]] = []
    for feature, op in high_risk_features:
        if feature not in df.columns:
            continue
        values = pd.to_numeric(df.loc[active, feature], errors="coerce").dropna()
        if values.nunique() < 4:
            continue
        target_qs = [q for q in qs if (q >= 0.5 if op == "ge" else q <= 0.5)]
        for q in target_qs:
            threshold = float(values.quantile(q))
            series = pd.to_numeric(df[feature], errors="coerce")
            mask = active & ((series >= threshold) if op == "ge" else (series <= threshold))
            if int(mask.sum()) >= min_filtered_active:
                feature_masks.append((feature, f"{op}_q{int(round(q * 100)):02d}", threshold, mask))

    for i, left in enumerate(feature_masks):
        for right in feature_masks[i + 1 :]:
            left_feature, left_name, left_threshold, left_mask = left
            right_feature, right_name, right_threshold, right_mask = right
            if left_feature == right_feature:
                continue
            mask = left_mask & right_mask
            if int(mask.sum()) < min_filtered_active:
                continue
            yield FilterSpec(
                name=f"skip_combo_{left_feature}_{left_name}__{right_feature}_{right_name}",
                expression=f"{left_feature} {left_name} {left_threshold:.8g} and {right_feature} {right_name} {right_threshold:.8g}",
                mask=mask,
            )


def _outcome_counts(original: pd.DataFrame, mask: pd.Series) -> dict[str, Any]:
    active = _bool_series(original, "is_active_trade", default=True)
    selected = active & mask.reindex(original.index, fill_value=False)
    returns = _numeric_series(original, "trade_return")
    hit_tp = _bool_series(original, "hit_take_profit")
    hit_sl = _bool_series(original, "hit_stop_loss")
    winners = active & (returns > 0)
    losers = active & (returns < 0)
    selected_count = int(selected.sum())
    selected_losers = int((selected & losers).sum())
    selected_winners = int((selected & winners).sum())
    return {
        "filtered_active_trades": selected_count,
        "filtered_losers": selected_losers,
        "filtered_winners": selected_winners,
        "filtered_take_profits": int((selected & hit_tp).sum()),
        "filtered_stop_losses": int((selected & hit_sl).sum()),
        "filtered_loss_capture_rate": float(selected_losers / losers.sum()) if losers.any() else 0.0,
        "filtered_win_sacrifice_rate": float(selected_winners / winners.sum()) if winners.any() else 0.0,
        "filtered_loser_share": float(selected_losers / selected_count) if selected_count else 0.0,
    }


def _write_report(path: Path, baseline: dict[str, Any], summary: pd.DataFrame, top: int) -> None:
    lines = [
        "# Phoenix Win-Rate Filter Experiment",
        "",
        "## Baseline",
        "",
        f"- active_win_rate: {baseline['active_win_rate']:.2%}",
        f"- active_avg_return: {baseline['active_avg_return']:.4%}",
        f"- portfolio_return_by_date_mean: {baseline['portfolio_return_by_date_mean']:.4%}",
        f"- cash_weight: {baseline['cash_weight']:.2%}",
        f"- portfolio_mdd: {baseline['portfolio_mdd']:.2%}",
        f"- stop_loss_rate: {baseline['stop_loss_rate']:.2%}",
        "",
        "## Top Candidates",
        "",
    ]
    cols = [
        "name",
        "expression",
        "active_win_rate",
        "win_rate_delta",
        "active_avg_return",
        "portfolio_return_by_date_mean",
        "cash_weight",
        "portfolio_mdd",
        "filtered_active_trades",
        "filtered_loss_capture_rate",
        "filtered_win_sacrifice_rate",
        "stop_loss_rate",
    ]
    if summary.empty:
        lines.append("No candidate passed constraints.")
    else:
        view = summary.head(top)[cols].copy()
        for col in [
            "active_win_rate",
            "win_rate_delta",
            "active_avg_return",
            "portfolio_return_by_date_mean",
            "cash_weight",
            "portfolio_mdd",
            "filtered_loss_capture_rate",
            "filtered_win_sacrifice_rate",
            "stop_loss_rate",
        ]:
            view[col] = view[col].map(lambda x: f"{x:.2%}")
        lines.extend(_markdown_table(view))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _markdown_table(df: pd.DataFrame) -> list[str]:
    headers = [str(col) for col in df.columns]
    rows = [[str(value) for value in row] for row in df.to_numpy()]
    widths = [
        max(len(headers[idx]), *(len(row[idx]) for row in rows)) if rows else len(headers[idx])
        for idx in range(len(headers))
    ]

    def fmt_row(values: list[str]) -> str:
        cells = [values[idx].ljust(widths[idx]) for idx in range(len(values))]
        return "| " + " | ".join(cells) + " |"

    table = [fmt_row(headers), "| " + " | ".join("-" * width for width in widths) + " |"]
    table.extend(fmt_row(row) for row in rows)
    return table


def main() -> None:
    args = _parse_args()
    trade_csv = Path(args.trade_csv)
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path("reports") / "win_rate_filter_experiment" / stamp
    output_dir.mkdir(parents=True, exist_ok=True)

    df = _read_trades(trade_csv)
    baseline = _summarize(df)
    qs = _quantiles(args.quantiles)
    specs = list(_category_specs(df, args.min_filtered_active))
    specs.extend(_numeric_specs(df, qs, args.min_filtered_active))
    specs.extend(_targeted_combo_specs(df, qs, args.min_filtered_active))

    rows: list[dict[str, Any]] = []
    filtered_frames: dict[str, pd.DataFrame] = {}
    for spec in specs:
        filtered = _apply_skip(df, spec.mask)
        metrics = _summarize(filtered)
        if metrics["n_active"] < args.min_active_trades:
            continue
        if metrics["cash_weight"] > args.max_cash_weight:
            continue
        if metrics["portfolio_return_by_date_mean"] < args.min_portfolio_mean:
            continue
        if metrics["portfolio_mdd"] > args.max_mdd:
            continue
        counts = _outcome_counts(df, spec.mask)
        row = {
            "name": spec.name,
            "expression": spec.expression,
            **metrics,
            **counts,
            "win_rate_delta": metrics["active_win_rate"] - baseline["active_win_rate"],
            "portfolio_mean_delta": metrics["portfolio_return_by_date_mean"] - baseline["portfolio_return_by_date_mean"],
            "active_avg_return_delta": metrics["active_avg_return"] - baseline["active_avg_return"],
            "mdd_delta": metrics["portfolio_mdd"] - baseline["portfolio_mdd"],
        }
        rows.append(row)
        filtered_frames[spec.name] = filtered

    summary = pd.DataFrame(rows)
    if not summary.empty:
        summary = summary.sort_values(
            by=[
                "active_win_rate",
                "portfolio_return_by_date_mean",
                "filtered_loss_capture_rate",
                "filtered_win_sacrifice_rate",
            ],
            ascending=[False, False, False, True],
        )

    summary_path = output_dir / "filter_summary.csv"
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")

    if not summary.empty:
        best_name = str(summary.iloc[0]["name"])
        filtered_frames[best_name].to_csv(output_dir / "best_filtered_slots.csv", index=False, encoding="utf-8-sig")

    metadata = {
        "input_csv": str(trade_csv),
        "output_dir": str(output_dir),
        "baseline": baseline,
        "constraints": {
            "max_cash_weight": args.max_cash_weight,
            "min_active_trades": args.min_active_trades,
            "min_portfolio_mean": args.min_portfolio_mean,
            "max_mdd": args.max_mdd,
            "min_filtered_active": args.min_filtered_active,
        },
        "candidate_count": int(len(specs)),
        "passed_count": int(len(summary)),
    }
    (output_dir / "summary.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _write_report(output_dir / "win_rate_filter_report.md", baseline, summary, args.top)

    print(f"input_csv={trade_csv}")
    print(f"output_dir={output_dir}")
    print(f"baseline_active_win_rate={baseline['active_win_rate']:.2%}")
    print(f"candidate_count={len(specs)}")
    print(f"passed_count={len(summary)}")
    if not summary.empty:
        best = summary.iloc[0]
        print(
            "best="
            f"{best['name']} active_win_rate={best['active_win_rate']:.2%} "
            f"portfolio_mean={best['portfolio_return_by_date_mean']:.4%} "
            f"cash={best['cash_weight']:.2%} mdd={best['portfolio_mdd']:.2%}"
        )
    print(f"summary={summary_path}")


if __name__ == "__main__":
    main()
