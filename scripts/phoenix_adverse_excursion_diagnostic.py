#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


NUMERIC_FEATURES = [
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
    "fwd_max_ret_5d",
    "fwd_max_ret_10d",
    "fwd_close_ret_5d",
    "fwd_close_ret_10d",
    "fwd_min_ret_5d",
    "fwd_min_ret_10d",
    "asof_dollar_volume",
    "gap_open_return",
    "max_high_return",
    "max_low_return",
    "trade_return",
    "net_return",
    "slot_return",
]

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
    "rank_mode",
    "label",
    "regime",
    "sector_etf",
    "filter_reason",
    "exit_reason",
]


@dataclass(frozen=True)
class OutcomeSummary:
    input_csv: str
    output_dir: str
    rows: int
    active_trades: int
    take_profit_trades: int
    stop_loss_trades: int
    time_exit_trades: int
    active_win_rate: float
    active_avg_return: float
    active_median_return: float
    stop_loss_rate: float
    take_profit_rate: float
    time_exit_rate: float


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnose adverse-excursion patterns in Phoenix benchmark trade simulation output."
    )
    parser.add_argument(
        "--benchmark-dir",
        default="reports/benchmark_20260710_174326",
        help="Benchmark report directory containing benchmark_trade_sim.csv.",
    )
    parser.add_argument(
        "--trade-csv",
        default=None,
        help="Explicit benchmark_trade_sim.csv path. Overrides --benchmark-dir.",
    )
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--top-segments", type=int, default=30)
    parser.add_argument("--min-segment-trades", type=int, default=8)
    parser.add_argument("--json", action="store_true", help="print summary JSON only")
    return parser.parse_args()


def _trade_csv_path(args: argparse.Namespace) -> Path:
    if args.trade_csv:
        return Path(args.trade_csv)
    return Path(args.benchmark_dir) / "benchmark_trade_sim.csv"


def _read_trades(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"trade simulation CSV not found: {path}")
    df = pd.read_csv(path, encoding="utf-8-sig")
    if df.empty:
        raise ValueError(f"trade simulation CSV is empty: {path}")
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


def _classify_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    active = _bool_series(out, "is_active_trade", default=True)
    cash = _bool_series(out, "is_cash_slot", default=False)
    hit_tp = _bool_series(out, "hit_take_profit")
    hit_sl = _bool_series(out, "hit_stop_loss")
    exit_reason = out.get("exit_reason", pd.Series("", index=out.index)).astype(str).str.upper()
    trade_return = _numeric_series(out, "trade_return")

    outcome = pd.Series("time_exit", index=out.index, dtype="object")
    outcome.loc[hit_tp | exit_reason.eq("TP")] = "take_profit"
    outcome.loc[hit_sl | exit_reason.eq("SL")] = "stop_loss"
    outcome.loc[~active] = "inactive"
    outcome.loc[cash] = "cash_slot"

    out["diagnostic_outcome"] = outcome
    out["diagnostic_is_winner"] = active & (trade_return > 0)
    out["diagnostic_is_loser"] = active & (trade_return < 0)
    out["diagnostic_is_stop_loss"] = active & outcome.eq("stop_loss")
    out["diagnostic_is_take_profit"] = active & outcome.eq("take_profit")
    return out


def _summarize_numeric(df: pd.DataFrame) -> pd.DataFrame:
    active = df[_bool_series(df, "is_active_trade", default=True)].copy()
    available = [col for col in NUMERIC_FEATURES if col in active.columns]
    rows: list[dict[str, Any]] = []
    for feature in available:
        values = pd.to_numeric(active[feature], errors="coerce")
        for outcome, group in active.assign(_feature_value=values).groupby("diagnostic_outcome", dropna=False):
            group_values = group["_feature_value"].dropna()
            if group_values.empty:
                continue
            rows.append(
                {
                    "feature": feature,
                    "outcome": outcome,
                    "count": int(group_values.count()),
                    "mean": float(group_values.mean()),
                    "median": float(group_values.median()),
                    "std": float(group_values.std(ddof=0)),
                    "p25": float(group_values.quantile(0.25)),
                    "p75": float(group_values.quantile(0.75)),
                }
            )
    return pd.DataFrame(rows)


def _compare_stop_loss_vs_winners(df: pd.DataFrame, features: list[str] | None = None) -> pd.DataFrame:
    active = df[_bool_series(df, "is_active_trade", default=True)].copy()
    stop_loss = active[active["diagnostic_is_stop_loss"]]
    winners = active[active["diagnostic_is_winner"]]
    rows: list[dict[str, Any]] = []
    feature_list = features or NUMERIC_FEATURES
    for feature in [col for col in feature_list if col in active.columns]:
        sl_values = pd.to_numeric(stop_loss[feature], errors="coerce").dropna()
        win_values = pd.to_numeric(winners[feature], errors="coerce").dropna()
        if sl_values.empty or win_values.empty:
            continue
        rows.append(
            {
                "feature": feature,
                "stop_loss_count": int(sl_values.count()),
                "winner_count": int(win_values.count()),
                "stop_loss_mean": float(sl_values.mean()),
                "winner_mean": float(win_values.mean()),
                "mean_delta_stop_loss_minus_winner": float(sl_values.mean() - win_values.mean()),
                "stop_loss_median": float(sl_values.median()),
                "winner_median": float(win_values.median()),
                "median_delta_stop_loss_minus_winner": float(sl_values.median() - win_values.median()),
            }
        )
    result = pd.DataFrame(rows)
    if not result.empty:
        result["abs_mean_delta"] = result["mean_delta_stop_loss_minus_winner"].abs()
        result = result.sort_values(["abs_mean_delta", "feature"], ascending=[False, True]).drop(columns=["abs_mean_delta"])
    return result


def _category_summary(df: pd.DataFrame, min_trades: int) -> pd.DataFrame:
    active = df[_bool_series(df, "is_active_trade", default=True)].copy()
    trade_return = _numeric_series(active, "trade_return")
    rows: list[dict[str, Any]] = []
    for feature in [col for col in CATEGORY_FEATURES if col in active.columns]:
        values = active[feature].fillna("NA").astype(str)
        for value, group in active.assign(_category_value=values, _trade_return=trade_return).groupby("_category_value", dropna=False):
            n = len(group)
            if n < min_trades:
                continue
            stop_loss = int(group["diagnostic_is_stop_loss"].sum())
            take_profit = int(group["diagnostic_is_take_profit"].sum())
            winners = int(group["diagnostic_is_winner"].sum())
            rows.append(
                {
                    "feature": feature,
                    "value": value,
                    "trades": int(n),
                    "stop_loss_trades": stop_loss,
                    "take_profit_trades": take_profit,
                    "winner_trades": winners,
                    "stop_loss_rate": stop_loss / n if n else 0.0,
                    "take_profit_rate": take_profit / n if n else 0.0,
                    "win_rate": winners / n if n else 0.0,
                    "avg_trade_return": float(group["_trade_return"].mean()),
                    "median_trade_return": float(group["_trade_return"].median()),
                }
            )
    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(["stop_loss_rate", "trades"], ascending=[False, False])
    return result


def _quantile_segment_summary(df: pd.DataFrame, min_trades: int, features: list[str] | None = None) -> pd.DataFrame:
    active = df[_bool_series(df, "is_active_trade", default=True)].copy()
    rows: list[dict[str, Any]] = []
    feature_list = features or NUMERIC_FEATURES
    for feature in [col for col in feature_list if col in active.columns]:
        values = pd.to_numeric(active[feature], errors="coerce")
        if values.nunique(dropna=True) < 4:
            continue
        try:
            buckets = pd.qcut(values, q=4, duplicates="drop")
        except ValueError:
            continue
        work = active.assign(_bucket=buckets, _value=values, _trade_return=_numeric_series(active, "trade_return"))
        for bucket, group in work.dropna(subset=["_bucket"]).groupby("_bucket", observed=True):
            n = len(group)
            if n < min_trades:
                continue
            stop_loss = int(group["diagnostic_is_stop_loss"].sum())
            take_profit = int(group["diagnostic_is_take_profit"].sum())
            rows.append(
                {
                    "feature": feature,
                    "bucket": str(bucket),
                    "trades": int(n),
                    "value_min": float(group["_value"].min()),
                    "value_max": float(group["_value"].max()),
                    "stop_loss_trades": stop_loss,
                    "take_profit_trades": take_profit,
                    "stop_loss_rate": stop_loss / n if n else 0.0,
                    "take_profit_rate": take_profit / n if n else 0.0,
                    "avg_trade_return": float(group["_trade_return"].mean()),
                    "median_trade_return": float(group["_trade_return"].median()),
                }
            )
    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(["stop_loss_rate", "trades"], ascending=[False, False])
    return result


def _outcome_summary(df: pd.DataFrame, input_csv: Path, output_dir: Path) -> OutcomeSummary:
    active = df[_bool_series(df, "is_active_trade", default=True)].copy()
    trade_return = _numeric_series(active, "trade_return")
    active_count = int(len(active))
    stop_loss = int(active["diagnostic_is_stop_loss"].sum())
    take_profit = int(active["diagnostic_is_take_profit"].sum())
    time_exit = int(active["diagnostic_outcome"].eq("time_exit").sum())
    winners = int(active["diagnostic_is_winner"].sum())
    return OutcomeSummary(
        input_csv=str(input_csv),
        output_dir=str(output_dir),
        rows=int(len(df)),
        active_trades=active_count,
        take_profit_trades=take_profit,
        stop_loss_trades=stop_loss,
        time_exit_trades=time_exit,
        active_win_rate=winners / active_count if active_count else 0.0,
        active_avg_return=float(trade_return.mean()) if active_count else 0.0,
        active_median_return=float(trade_return.median()) if active_count else 0.0,
        stop_loss_rate=stop_loss / active_count if active_count else 0.0,
        take_profit_rate=take_profit / active_count if active_count else 0.0,
        time_exit_rate=time_exit / active_count if active_count else 0.0,
    )


def _write_markdown(
    path: Path,
    summary: OutcomeSummary,
    stop_loss_vs_winners: pd.DataFrame,
    category_summary: pd.DataFrame,
    quantile_summary: pd.DataFrame,
    pretrade_stop_loss_vs_winners: pd.DataFrame,
    pretrade_quantile_summary: pd.DataFrame,
    top_segments: int,
) -> None:
    def table_lines(frame: pd.DataFrame, columns: list[str]) -> list[str]:
        view = frame[columns].copy()
        for col in view.columns:
            if pd.api.types.is_float_dtype(view[col]):
                view[col] = view[col].map(lambda value: f"{value:.6g}")
        header = "| " + " | ".join(columns) + " |"
        divider = "| " + " | ".join(["---"] * len(columns)) + " |"
        body = [
            "| " + " | ".join(str(row[col]) for col in columns) + " |"
            for _, row in view.iterrows()
        ]
        return [header, divider, *body]

    lines = [
        "# Phoenix Adverse Excursion Diagnostic",
        "",
        f"- input_csv: `{summary.input_csv}`",
        f"- rows: `{summary.rows}`",
        f"- active_trades: `{summary.active_trades}`",
        f"- take_profit_rate: `{summary.take_profit_rate:.2%}`",
        f"- stop_loss_rate: `{summary.stop_loss_rate:.2%}`",
        f"- time_exit_rate: `{summary.time_exit_rate:.2%}`",
        f"- active_avg_return: `{summary.active_avg_return:.4%}`",
        f"- active_median_return: `{summary.active_median_return:.4%}`",
        "",
        "## Pre-Trade Numeric Differences",
        "",
    ]
    if pretrade_stop_loss_vs_winners.empty:
        lines.append("No pre-trade stop-loss vs winner comparison rows were available.")
    else:
        cols = [
            "feature",
            "stop_loss_mean",
            "winner_mean",
            "mean_delta_stop_loss_minus_winner",
            "stop_loss_median",
            "winner_median",
        ]
        lines.extend(table_lines(pretrade_stop_loss_vs_winners.head(top_segments), cols))

    lines.extend(["", "## Highest Stop-Loss Pre-Trade Numeric Buckets", ""])
    if pretrade_quantile_summary.empty:
        lines.append("No pre-trade numeric bucket rows met the minimum trade threshold.")
    else:
        cols = ["feature", "bucket", "trades", "stop_loss_rate", "take_profit_rate", "avg_trade_return"]
        lines.extend(table_lines(pretrade_quantile_summary.head(top_segments), cols))

    lines.extend(["", "## Largest Numeric Differences", ""])
    if stop_loss_vs_winners.empty:
        lines.append("No numeric stop-loss vs winner comparison rows were available.")
    else:
        cols = [
            "feature",
            "stop_loss_mean",
            "winner_mean",
            "mean_delta_stop_loss_minus_winner",
            "stop_loss_median",
            "winner_median",
        ]
        lines.extend(table_lines(stop_loss_vs_winners.head(top_segments), cols))

    lines.extend(["", "## Highest Stop-Loss Categorical Segments", ""])
    if category_summary.empty:
        lines.append("No categorical segment rows met the minimum trade threshold.")
    else:
        cols = ["feature", "value", "trades", "stop_loss_rate", "take_profit_rate", "avg_trade_return"]
        lines.extend(table_lines(category_summary.head(top_segments), cols))

    lines.extend(["", "## Highest Stop-Loss Numeric Buckets", ""])
    if quantile_summary.empty:
        lines.append("No numeric bucket rows met the minimum trade threshold.")
    else:
        cols = ["feature", "bucket", "trades", "stop_loss_rate", "take_profit_rate", "avg_trade_return"]
        lines.extend(table_lines(quantile_summary.head(top_segments), cols))

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    input_csv = _trade_csv_path(args)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir or Path("reports") / "adverse_excursion" / stamp)
    output_dir.mkdir(parents=True, exist_ok=True)

    trades = _classify_outcomes(_read_trades(input_csv))
    numeric_summary = _summarize_numeric(trades)
    stop_loss_vs_winners = _compare_stop_loss_vs_winners(trades)
    pretrade_stop_loss_vs_winners = _compare_stop_loss_vs_winners(trades, features=PRETRADE_NUMERIC_FEATURES)
    category_summary = _category_summary(trades, min_trades=args.min_segment_trades)
    quantile_summary = _quantile_segment_summary(trades, min_trades=args.min_segment_trades)
    pretrade_quantile_summary = _quantile_segment_summary(
        trades,
        min_trades=args.min_segment_trades,
        features=PRETRADE_NUMERIC_FEATURES,
    )
    summary = _outcome_summary(trades, input_csv=input_csv, output_dir=output_dir)

    trades.to_csv(output_dir / "trade_outcomes.csv", index=False, encoding="utf-8-sig")
    numeric_summary.to_csv(output_dir / "numeric_feature_by_outcome.csv", index=False, encoding="utf-8-sig")
    stop_loss_vs_winners.to_csv(output_dir / "stop_loss_vs_winners.csv", index=False, encoding="utf-8-sig")
    pretrade_stop_loss_vs_winners.to_csv(output_dir / "pretrade_stop_loss_vs_winners.csv", index=False, encoding="utf-8-sig")
    category_summary.to_csv(output_dir / "categorical_segments.csv", index=False, encoding="utf-8-sig")
    quantile_summary.to_csv(output_dir / "numeric_quantile_segments.csv", index=False, encoding="utf-8-sig")
    pretrade_quantile_summary.to_csv(output_dir / "pretrade_numeric_quantile_segments.csv", index=False, encoding="utf-8-sig")
    (output_dir / "summary.json").write_text(json.dumps(asdict(summary), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_markdown(
        output_dir / "adverse_excursion_report.md",
        summary=summary,
        stop_loss_vs_winners=stop_loss_vs_winners,
        category_summary=category_summary,
        quantile_summary=quantile_summary,
        pretrade_stop_loss_vs_winners=pretrade_stop_loss_vs_winners,
        pretrade_quantile_summary=pretrade_quantile_summary,
        top_segments=args.top_segments,
    )

    if args.json:
        print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    else:
        print(f"input_csv={input_csv}")
        print(f"output_dir={output_dir}")
        print(f"active_trades={summary.active_trades}")
        print(f"stop_loss_rate={summary.stop_loss_rate:.2%}")
        print(f"take_profit_rate={summary.take_profit_rate:.2%}")
        print(f"active_avg_return={summary.active_avg_return:.4%}")
        print(f"report={output_dir / 'adverse_excursion_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
