#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    penalties: dict[str, float]
    skips: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScenarioSummary:
    scenario: str
    layer: str
    description: str
    n_dates: int
    n_slots: int
    n_active: int
    cash_slots: int
    cash_weight: float
    avg_slot_return: float
    median_slot_return: float
    portfolio_return_by_date_mean: float
    portfolio_return_by_date_median: float
    portfolio_positive_date_rate: float
    portfolio_mdd: float
    take_profit_rate: float
    stop_loss_rate: float
    time_exit_rate: float
    win_rate: float


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline Phoenix adverse-risk filter and rerank experiment.")
    parser.add_argument("--benchmark-dir", default="reports/benchmark_20260710_174326")
    parser.add_argument("--ranked-csv", default=None)
    parser.add_argument("--trade-csv", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--candidate-rank-max", type=int, default=10)
    parser.add_argument("--take-profit", type=float, default=0.10)
    parser.add_argument("--stop-loss", type=float, default=0.05)
    parser.add_argument("--hold-days", type=int, default=10)
    parser.add_argument("--roundtrip-cost-bps", type=float, default=33.0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path, encoding="utf-8-sig")
    if df.empty:
        raise ValueError(f"empty CSV: {path}")
    return df


def _num(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if col not in df.columns:
        return pd.Series(default, index=df.index, dtype="float64")
    return pd.to_numeric(df[col], errors="coerce")


def _bool(df: pd.DataFrame, col: str, default: bool = False) -> pd.Series:
    if col not in df.columns:
        return pd.Series(default, index=df.index)
    raw = df[col]
    if raw.dtype == bool:
        return raw.fillna(default)
    return raw.astype(str).str.lower().isin({"true", "1", "yes", "y"})


def _mdd(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    equity = (1.0 + returns.fillna(0.0)).cumprod()
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(abs(dd.min()))


def _condition_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    sector = df.get("sector_etf", pd.Series("", index=df.index)).fillna("").astype(str)
    regime = df.get("regime", pd.Series("", index=df.index)).fillna("").astype(str)
    label = df.get("label", pd.Series("", index=df.index)).fillna("").astype(str)
    confidence = _num(df, "confidence_score")
    risk = _num(df, "risk_score")
    market = _num(df, "market_score")
    final_rank = _num(df, "final_rank_score")
    similarity5 = _num(df, "similarity_hit_5d")
    gap = _num(df, "gap_open_return")
    rarity = _num(df, "pattern_rarity")
    sector_nlr = sector.eq("NLR")
    sector_xlk = sector.eq("XLK")
    sector_nlr_xlk = sector_nlr | sector_xlk
    neutral_mixed = regime.eq("Neutral / Mixed")
    broad_bull = regime.eq("Broad Bull")
    bear_trend = regime.eq("Bear Trend")
    risk_off = regime.eq("Risk Off")
    ai_growth = regime.eq("AI Growth Rotation")
    narrow_tech = regime.eq("Narrow Tech Rotation")
    return {
        "sector_nlr": sector_nlr,
        "sector_xlk": sector_xlk,
        "sector_nlr_xlk": sector_nlr_xlk,
        "sector_smh": sector.eq("SMH"),
        "bear_trend": bear_trend,
        "risk_off": risk_off,
        "neutral_mixed": neutral_mixed,
        "broad_bull": broad_bull,
        "ai_growth_rotation": ai_growth,
        "narrow_tech_rotation": narrow_tech,
        "nlr_xlk_neutral_mixed": sector_nlr_xlk & neutral_mixed,
        "nlr_xlk_broad_bull": sector_nlr_xlk & broad_bull,
        "nlr_xlk_bear_trend": sector_nlr_xlk & bear_trend,
        "nlr_xlk_risk_off": sector_nlr_xlk & risk_off,
        "nlr_xlk_ai_growth": sector_nlr_xlk & ai_growth,
        "nlr_xlk_narrow_tech": sector_nlr_xlk & narrow_tech,
        "nlr_xlk_neutral_bear": sector_nlr_xlk & (neutral_mixed | bear_trend),
        "nlr_xlk_neutral_broad_bear": sector_nlr_xlk & (neutral_mixed | broad_bull | bear_trend),
        "nlr_xlk_risk_bear": sector_nlr_xlk & (risk_off | bear_trend),
        "nlr_xlk_risk_neutral_bear": sector_nlr_xlk & (risk_off | neutral_mixed | bear_trend),
        "nlr_xlk_risk_neutral_broad_bear": sector_nlr_xlk & (risk_off | neutral_mixed | broad_bull | bear_trend),
        "nlr_xlk_low_score": sector_nlr_xlk & final_rank.le(82.0),
        "nlr_xlk_neutral_or_low_score": sector_nlr_xlk & (neutral_mixed | final_rank.le(82.0)),
        "label_observe": label.eq("관찰"),
        "confidence_low": confidence.le(93.031),
        "risk_mid": risk.gt(53.886) & risk.le(65.167),
        "market_mid": market.gt(56.647) & market.le(63.907),
        "similarity5_mid_high": similarity5.gt(0.688) & similarity5.le(0.770),
        "gap_high": gap.gt(0.0115),
        "gap_flat_to_small": gap.gt(-0.0132) & gap.le(0.000946),
        "rarity_high": rarity.gt(91.205),
    }


def _scenario_defs() -> list[Scenario]:
    return [
        Scenario("baseline", "No risk penalty or skip.", {}),
        Scenario(
            "hard_skip_bear_only",
            "Skip Bear Trend only; unfilled slots become cash in filter mode.",
            {},
            skips=("bear_trend",),
        ),
        Scenario(
            "hard_skip_nlr_only",
            "Skip NLR only; unfilled slots become cash in filter mode.",
            {},
            skips=("sector_nlr",),
        ),
        Scenario(
            "hard_skip_xlk_only",
            "Skip XLK only; unfilled slots become cash in filter mode.",
            {},
            skips=("sector_xlk",),
        ),
        Scenario(
            "hard_skip_nlr_xlk",
            "Skip NLR and XLK; unfilled slots become cash in filter mode.",
            {},
            skips=("sector_nlr", "sector_xlk"),
        ),
        Scenario(
            "hard_skip_bear_nlr_xlk",
            "Skip Bear Trend, NLR, and XLK; unfilled slots become cash in filter mode.",
            {},
            skips=("bear_trend", "sector_nlr", "sector_xlk"),
        ),
        Scenario(
            "conditional_skip_nlr_xlk_neutral",
            "Skip NLR/XLK only in Neutral / Mixed regime.",
            {},
            skips=("nlr_xlk_neutral_mixed",),
        ),
        Scenario(
            "conditional_skip_nlr_xlk_broad_bull",
            "Skip NLR/XLK only in Broad Bull regime.",
            {},
            skips=("nlr_xlk_broad_bull",),
        ),
        Scenario(
            "conditional_skip_nlr_xlk_bear",
            "Skip NLR/XLK only in Bear Trend regime.",
            {},
            skips=("nlr_xlk_bear_trend",),
        ),
        Scenario(
            "conditional_skip_nlr_xlk_risk_off",
            "Skip NLR/XLK only in Risk Off regime.",
            {},
            skips=("nlr_xlk_risk_off",),
        ),
        Scenario(
            "conditional_skip_nlr_xlk_ai_growth",
            "Skip NLR/XLK only in AI Growth Rotation regime.",
            {},
            skips=("nlr_xlk_ai_growth",),
        ),
        Scenario(
            "conditional_skip_nlr_xlk_narrow_tech",
            "Skip NLR/XLK only in Narrow Tech Rotation regime.",
            {},
            skips=("nlr_xlk_narrow_tech",),
        ),
        Scenario(
            "conditional_skip_nlr_xlk_neutral_bear",
            "Skip NLR/XLK only in Neutral / Mixed or Bear Trend regimes.",
            {},
            skips=("nlr_xlk_neutral_bear",),
        ),
        Scenario(
            "conditional_skip_nlr_xlk_neutral_broad_bear",
            "Skip NLR/XLK only in Neutral / Mixed, Broad Bull, or Bear Trend regimes.",
            {},
            skips=("nlr_xlk_neutral_broad_bear",),
        ),
        Scenario(
            "conditional_skip_nlr_xlk_risk_bear",
            "Skip NLR/XLK only in Risk Off or Bear Trend regimes.",
            {},
            skips=("nlr_xlk_risk_bear",),
        ),
        Scenario(
            "conditional_skip_nlr_xlk_risk_neutral_bear",
            "Skip NLR/XLK only in Risk Off, Neutral / Mixed, or Bear Trend regimes.",
            {},
            skips=("nlr_xlk_risk_neutral_bear",),
        ),
        Scenario(
            "conditional_skip_nlr_xlk_risk_neutral_broad_bear",
            "Skip NLR/XLK only in Risk Off, Neutral / Mixed, Broad Bull, or Bear Trend regimes.",
            {},
            skips=("nlr_xlk_risk_neutral_broad_bear",),
        ),
        Scenario(
            "conditional_skip_nlr_xlk_low_score",
            "Skip NLR/XLK only when final rank score is <= 82.",
            {},
            skips=("nlr_xlk_low_score",),
        ),
        Scenario(
            "conditional_skip_nlr_xlk_neutral_or_low_score",
            "Skip NLR/XLK in Neutral / Mixed regime or when final rank score is <= 82.",
            {},
            skips=("nlr_xlk_neutral_or_low_score",),
        ),
        Scenario(
            "sector_only_penalty",
            "Penalize high stop-loss sectors from diagnostics.",
            {"sector_nlr": 6.0, "sector_xlk": 4.0, "sector_smh": 2.0},
        ),
        Scenario(
            "light_risk_penalty",
            "Light sector, regime, gap, confidence, and risk penalty.",
            {
                "sector_nlr": 3.0,
                "sector_xlk": 2.0,
                "bear_trend": 5.0,
                "gap_high": 2.0,
                "confidence_low": 1.5,
                "risk_mid": 1.0,
            },
        ),
        Scenario(
            "segments_v1_penalty",
            "Diagnostic segment penalty without hard skips.",
            {
                "sector_nlr": 6.0,
                "sector_xlk": 4.0,
                "sector_smh": 2.0,
                "bear_trend": 8.0,
                "neutral_mixed": 2.0,
                "broad_bull": 2.0,
                "label_observe": 1.5,
                "gap_high": 3.0,
                "confidence_low": 3.0,
                "risk_mid": 2.0,
                "market_mid": 2.0,
                "similarity5_mid_high": 1.5,
            },
        ),
        Scenario(
            "segments_v1_skip_bear",
            "segments_v1 penalty plus Bear Trend skip.",
            {
                "sector_nlr": 6.0,
                "sector_xlk": 4.0,
                "sector_smh": 2.0,
                "neutral_mixed": 2.0,
                "broad_bull": 2.0,
                "label_observe": 1.5,
                "gap_high": 3.0,
                "confidence_low": 3.0,
                "risk_mid": 2.0,
                "market_mid": 2.0,
                "similarity5_mid_high": 1.5,
            },
            skips=("bear_trend",),
        ),
        Scenario(
            "nuclear_soft_avoid",
            "Strong NLR penalty, mild XLK/gap penalty.",
            {"sector_nlr": 8.0, "sector_xlk": 2.0, "gap_high": 2.0},
        ),
    ]


def _apply_scenario(df: pd.DataFrame, scenario: Scenario) -> pd.DataFrame:
    out = df.copy()
    masks = _condition_masks(out)
    penalty = pd.Series(0.0, index=out.index, dtype="float64")
    matched: list[pd.Series] = []
    for name, amount in scenario.penalties.items():
        mask = masks.get(name)
        if mask is None:
            continue
        penalty = penalty + mask.astype(float) * float(amount)
        matched.append(mask.rename(name))
    skip = pd.Series(False, index=out.index)
    for name in scenario.skips:
        mask = masks.get(name)
        if mask is not None:
            skip = skip | mask
            matched.append(mask.rename(f"skip_{name}"))
    base_score = _num(out, "final_rank_score")
    out["adverse_risk_penalty"] = penalty
    out["adverse_adjusted_score"] = base_score - penalty
    out["adverse_skip"] = skip
    if matched:
        reason_frame = pd.concat(matched, axis=1)
        out["adverse_reasons"] = reason_frame.apply(lambda row: ";".join(row.index[row.to_numpy(dtype=bool)]), axis=1)
    else:
        out["adverse_reasons"] = ""
    return out


def _proxy_returns(df: pd.DataFrame, take_profit: float, stop_loss: float, hold_days: int, cost_bps: float) -> pd.DataFrame:
    out = df.copy()
    max_col = f"fwd_max_ret_{hold_days}d"
    min_col = f"fwd_min_ret_{hold_days}d"
    close_col = f"fwd_close_ret_{hold_days}d"
    if max_col not in out.columns or min_col not in out.columns or close_col not in out.columns:
        raise ValueError(f"missing proxy columns for hold_days={hold_days}: {max_col}, {min_col}, {close_col}")
    fwd_max = _num(out, max_col)
    fwd_min = _num(out, min_col)
    fwd_close = _num(out, close_col)
    gross = fwd_close.fillna(0.0).clip(lower=-0.95)
    exit_reason = pd.Series("TIME", index=out.index, dtype="object")
    stop_mask = fwd_min.le(-stop_loss)
    tp_mask = ~stop_mask & fwd_max.ge(take_profit)
    gross.loc[stop_mask] = -stop_loss
    gross.loc[tp_mask] = take_profit
    exit_reason.loc[stop_mask] = "SL"
    exit_reason.loc[tp_mask] = "TP"
    out["proxy_gross_return"] = gross
    out["proxy_return"] = gross - (cost_bps / 10000.0)
    out["proxy_exit_reason"] = exit_reason
    return out


def _summarize_slots(slots: pd.DataFrame, *, scenario: Scenario, layer: str, top_n: int, return_col: str, exit_col: str) -> ScenarioSummary:
    dates = sorted(slots["as_of"].dropna().astype(str).unique())
    n_dates = len(dates)
    n_slots = n_dates * top_n
    cash_mask = _bool(slots, "is_cash_proxy") | _bool(slots, "is_cash_slot")
    active = slots[~cash_mask].copy()
    returns = pd.to_numeric(slots[return_col], errors="coerce").fillna(0.0)
    active_returns = pd.to_numeric(active[return_col], errors="coerce").fillna(0.0)
    by_date = slots.assign(_ret=returns).groupby("as_of", dropna=False)["_ret"].mean().sort_index()
    exits = active.get(exit_col, pd.Series("", index=active.index)).fillna("").astype(str).str.upper()
    n_active = len(active)
    cash_slots = n_slots - n_active
    return ScenarioSummary(
        scenario=scenario.name,
        layer=layer,
        description=scenario.description,
        n_dates=n_dates,
        n_slots=n_slots,
        n_active=n_active,
        cash_slots=cash_slots,
        cash_weight=cash_slots / n_slots if n_slots else 0.0,
        avg_slot_return=float(returns.mean()) if len(returns) else 0.0,
        median_slot_return=float(returns.median()) if len(returns) else 0.0,
        portfolio_return_by_date_mean=float(by_date.mean()) if len(by_date) else 0.0,
        portfolio_return_by_date_median=float(by_date.median()) if len(by_date) else 0.0,
        portfolio_positive_date_rate=float((by_date > 0).mean()) if len(by_date) else 0.0,
        portfolio_mdd=_mdd(by_date),
        take_profit_rate=float(exits.eq("TP").mean()) if n_active else 0.0,
        stop_loss_rate=float(exits.eq("SL").mean()) if n_active else 0.0,
        time_exit_rate=float(exits.eq("TIME").mean()) if n_active else 0.0,
        win_rate=float((active_returns > 0).mean()) if n_active else 0.0,
    )


def _rerank_slots(ranked: pd.DataFrame, scenario: Scenario, top_n: int) -> pd.DataFrame:
    work = _apply_scenario(ranked, scenario)
    picked: list[pd.DataFrame] = []
    for as_of, group in work.groupby("as_of", sort=True):
        eligible = group[~group["adverse_skip"]].sort_values(
            ["adverse_adjusted_score", "final_rank_score"], ascending=[False, False]
        )
        selected = eligible.head(top_n).copy()
        selected["adverse_selected_rank"] = range(1, len(selected) + 1)
        picked.append(selected)
        missing = top_n - len(selected)
        if missing > 0:
            cash = pd.DataFrame({
                "as_of": [as_of] * missing,
                "ticker": ["CASH"] * missing,
                "proxy_return": [0.0] * missing,
                "proxy_exit_reason": ["CASH"] * missing,
                "is_cash_proxy": [True] * missing,
                "adverse_selected_rank": list(range(len(selected) + 1, top_n + 1)),
            })
            picked.append(cash)
    slots = pd.concat(picked, ignore_index=True) if picked else pd.DataFrame()
    if "is_cash_proxy" not in slots.columns:
        slots["is_cash_proxy"] = False
    return slots


def _actual_filter_slots(trades: pd.DataFrame, scenario: Scenario, top_n: int) -> pd.DataFrame:
    work = _apply_scenario(trades, scenario)
    work["actual_filtered_return"] = pd.to_numeric(work.get("trade_return", 0.0), errors="coerce").fillna(0.0)
    work["actual_filtered_exit"] = work.get("exit_reason", pd.Series("", index=work.index)).fillna("").astype(str)
    skipped = work["adverse_skip"]
    existing_cash = _bool(work, "is_cash_slot")
    cash_mask = skipped | existing_cash
    work.loc[cash_mask, "actual_filtered_return"] = 0.0
    work.loc[cash_mask, "actual_filtered_exit"] = "CASH"
    work["is_cash_proxy"] = cash_mask
    return work


def _write_markdown(path: Path, summaries: pd.DataFrame, baseline_name: str) -> None:
    def fmt(value: Any) -> str:
        if isinstance(value, float):
            return f"{value:.6g}"
        return str(value)

    cols = [
        "scenario",
        "layer",
        "n_active",
        "cash_weight",
        "portfolio_return_by_date_mean",
        "portfolio_mdd",
        "stop_loss_rate",
        "take_profit_rate",
        "win_rate",
    ]
    lines = [
        "# Phoenix Risk Filter Experiment",
        "",
        "This is an offline screening report. Rerank rows use a forward-return proxy, not the full trade simulator.",
        "",
        f"Baseline scenario: `{baseline_name}`",
        "",
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join(["---"] * len(cols)) + " |",
    ]
    for _, row in summaries[cols].iterrows():
        lines.append("| " + " | ".join(fmt(row[col]) for col in cols) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    benchmark_dir = Path(args.benchmark_dir)
    ranked_csv = Path(args.ranked_csv) if args.ranked_csv else benchmark_dir / "benchmark_ranked_detail.csv"
    trade_csv = Path(args.trade_csv) if args.trade_csv else benchmark_dir / "benchmark_trade_sim.csv"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir or Path("reports") / "risk_filter_experiment" / stamp)
    output_dir.mkdir(parents=True, exist_ok=True)

    ranked = _read_csv(ranked_csv)
    trades = _read_csv(trade_csv)
    ranked = ranked[pd.to_numeric(ranked.get("rank", 999999), errors="coerce").le(args.candidate_rank_max)].copy()
    ranked = _proxy_returns(ranked, args.take_profit, args.stop_loss, args.hold_days, args.roundtrip_cost_bps)

    scenarios = _scenario_defs()
    summaries: list[ScenarioSummary] = []
    rerank_frames: list[pd.DataFrame] = []
    actual_frames: list[pd.DataFrame] = []

    for scenario in scenarios:
        rerank = _rerank_slots(ranked, scenario, args.top_n)
        rerank["scenario"] = scenario.name
        rerank_frames.append(rerank)
        summaries.append(
            _summarize_slots(
                rerank,
                scenario=scenario,
                layer="proxy_rerank_top_candidates",
                top_n=args.top_n,
                return_col="proxy_return",
                exit_col="proxy_exit_reason",
            )
        )

        actual = _actual_filter_slots(trades, scenario, args.top_n)
        actual["scenario"] = scenario.name
        actual_frames.append(actual)
        summaries.append(
            _summarize_slots(
                actual,
                scenario=scenario,
                layer="actual_top5_skip_to_cash",
                top_n=args.top_n,
                return_col="actual_filtered_return",
                exit_col="actual_filtered_exit",
            )
        )

    summary_df = pd.DataFrame([asdict(row) for row in summaries])
    summary_df = summary_df.sort_values(
        ["layer", "portfolio_return_by_date_mean", "portfolio_mdd"], ascending=[True, False, True]
    )
    rerank_df = pd.concat(rerank_frames, ignore_index=True) if rerank_frames else pd.DataFrame()
    actual_df = pd.concat(actual_frames, ignore_index=True) if actual_frames else pd.DataFrame()

    summary_df.to_csv(output_dir / "scenario_summary.csv", index=False, encoding="utf-8-sig")
    rerank_df.to_csv(output_dir / "rerank_selections.csv", index=False, encoding="utf-8-sig")
    actual_df.to_csv(output_dir / "actual_top5_filter_slots.csv", index=False, encoding="utf-8-sig")
    config = {
        "benchmark_dir": str(benchmark_dir),
        "ranked_csv": str(ranked_csv),
        "trade_csv": str(trade_csv),
        "top_n": args.top_n,
        "candidate_rank_max": args.candidate_rank_max,
        "take_profit": args.take_profit,
        "stop_loss": args.stop_loss,
        "hold_days": args.hold_days,
        "roundtrip_cost_bps": args.roundtrip_cost_bps,
        "scenarios": [asdict(s) for s in scenarios],
    }
    (output_dir / "experiment_config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_markdown(output_dir / "risk_filter_experiment_report.md", summary_df, "baseline")

    best_proxy = summary_df[summary_df["layer"].eq("proxy_rerank_top_candidates")].head(1).to_dict("records")
    best_actual = summary_df[summary_df["layer"].eq("actual_top5_skip_to_cash")].head(1).to_dict("records")
    result = {
        "output_dir": str(output_dir),
        "best_proxy": best_proxy[0] if best_proxy else {},
        "best_actual_filter": best_actual[0] if best_actual else {},
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"output_dir={output_dir}")
        if best_proxy:
            row = best_proxy[0]
            print(
                "best_proxy="
                f"{row['scenario']} mean={row['portfolio_return_by_date_mean']:.4%} "
                f"mdd={row['portfolio_mdd']:.2%} sl={row['stop_loss_rate']:.2%}"
            )
        if best_actual:
            row = best_actual[0]
            print(
                "best_actual_filter="
                f"{row['scenario']} mean={row['portfolio_return_by_date_mean']:.4%} "
                f"mdd={row['portfolio_mdd']:.2%} sl={row['stop_loss_rate']:.2%} cash={row['cash_weight']:.2%}"
            )
        print(f"report={output_dir / 'risk_filter_experiment_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
