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
import pandas as pd
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from phoenix_core.engines.intraday_context_engine import IntradayContextEngine
from phoenix_core.intraday_overlay_ranker import score_intraday_overlay_context
from phoenix_core.services.intraday_message_formatter import filter_intraday_overlay_contexts
from phoenix_core.services.telegram_message_formatter import parse_ranking_rows


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate/evaluate Phoenix top shadow comparison artifacts.")
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"), help="artifact date directory, YYYYMMDD")
    p.add_argument("--candidate-n", type=int, default=int(os.getenv("PHOENIX_TOP_CANDIDATE_N", "50")))
    p.add_argument("--top-n", type=int, default=int(os.getenv("PHOENIX_TOP_N", "10")))
    p.add_argument("--python-bin", default=sys.executable)
    p.add_argument("--config", default="config/config.yaml")
    p.add_argument("--period", default="3y")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--output-root", default="results/top_shadow_compare")
    p.add_argument("--evaluate-only", action="store_true", help="do not regenerate candidate CSVs; only evaluate existing files")
    return p.parse_args()


def _as_float(value: Any) -> float | None:
    try:
        v = float(value)
    except Exception:
        return None
    return v if math.isfinite(v) else None


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    if not fields:
        fields = ["ticker"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _run_daily_ranking(args: argparse.Namespace) -> tuple[str, list[dict[str, Any]]]:
    cmd = [args.python_bin, "main.py", "--top", "--top-n", str(max(args.candidate_n, args.top_n, 50)), "--config", args.config, "--period", args.period]
    if args.refresh:
        cmd.append("--refresh")
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900, shell=False)
    out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
    if proc.returncode:
        raise RuntimeError(f"ranking command failed code={proc.returncode}\n{out}")
    return out, parse_ranking_rows(out, max_rows=max(args.candidate_n, args.top_n, 50))


def _with_intraday(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    engine = IntradayContextEngine()
    tickers = [r["ticker"] for r in rows]
    contexts = filter_intraday_overlay_contexts(engine.analyze_many(tickers))
    ctx_by_ticker = {ctx.ticker.upper(): ctx for ctx in contexts}
    toplive = []
    hot = []
    for row in rows:
        ctx = ctx_by_ticker.get(row["ticker"])
        if not ctx:
            continue
        item = score_intraday_overlay_context(ctx, row["rank"])
        enriched = dict(row)
        enriched.update({
            "adjusted_score": round(item.adjusted_score, 4),
            "intraday_score": ctx.intraday_score,
            "intraday_risk_score": ctx.intraday_risk_score,
            "current_price": ctx.current_price,
            "current_vs_prev_close_pct": ctx.current_vs_prev_close_pct,
            "latest_10m_return_pct": ctx.latest_10m_return_pct,
            "latest_30m_return_pct": ctx.latest_30m_return_pct,
            "vwap_position_pct": ctx.vwap_position_pct,
            "above_vwap": ctx.above_vwap,
            "intraday_volume_ratio": ctx.intraday_volume_ratio,
        })
        toplive.append(enriched)
        momentum = ((ctx.latest_10m_return_pct is not None and ctx.latest_10m_return_pct > 0) or (ctx.latest_30m_return_pct is not None and ctx.latest_30m_return_pct > 0))
        if ctx.current_price is not None and ctx.current_vs_prev_close_pct is not None and ctx.current_vs_prev_close_pct > 0 and ctx.above_vwap is True and momentum and ctx.intraday_score >= int(os.getenv("PHOENIX_HOT_INTRADAY_MIN_SCORE", "55")):
            hot.append(enriched)
    toplive.sort(key=lambda r: (_as_float(r.get("adjusted_score")) or 0.0, -(int(r.get("rank") or 999))), reverse=True)
    hot.sort(key=lambda r: (_as_float(r.get("intraday_score")) or 0.0, -(_as_float(r.get("intraday_risk_score")) or 100.0)), reverse=True)
    return toplive, hot


def _download_daily(ticker: str):
    try:
        import yfinance as yf
        df = yf.download(ticker, period="3mo", interval="1d", auto_adjust=False, progress=False, threads=False)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    if hasattr(df.columns, "levels"):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    return df


def _evaluate_row(row: dict[str, Any], snapshot_date: pd.Timestamp) -> dict[str, Any]:
    ticker = row.get("ticker") or row.get("Ticker")
    out = dict(row)
    if not ticker:
        return out
    df = _download_daily(str(ticker))
    if df is None or df.empty or "Close" not in df.columns:
        out["eval_status"] = "NO_DATA"
        return out
    df = df.copy()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    closes = df["Close"].dropna()
    if closes.empty:
        out["eval_status"] = "NO_DATA"
        return out
    entry_series = closes[closes.index <= snapshot_date]
    if entry_series.empty:
        out["eval_status"] = "NO_ENTRY_BAR"
        return out
    entry = _as_float(row.get("entry")) or float(entry_series.iloc[-1])
    future = df[df.index > snapshot_date].head(5)
    if len(future) < 5:
        out["eval_status"] = "PENDING_FUTURE_BARS"
        out["future_bars"] = len(future)
        return out
    future_closes = future["Close"].dropna()
    future_highs = future["High"].dropna() if "High" in future.columns else future_closes
    future_lows = future["Low"].dropna() if "Low" in future.columns else future_closes
    for horizon in (1, 3, 5):
        if len(future_closes) >= horizon:
            out[f"ret_{horizon}d"] = float(future_closes.iloc[horizon - 1] / entry - 1.0)
    out["hit_5pct_5d"] = bool((future_highs / entry - 1.0 >= 0.05).any())
    first_tp = next((i for i, v in enumerate((future_highs / entry - 1.0).tolist(), 1) if v >= 0.05), None)
    first_sl = next((i for i, v in enumerate((future_lows / entry - 1.0).tolist(), 1) if v <= -0.03), None)
    out["stop_3pct_first_5d"] = bool(first_sl is not None and (first_tp is None or first_sl <= first_tp))
    out["max_drawdown_5d"] = float((future_lows / entry - 1.0).min())
    out["eval_status"] = "OK"
    return out


def _mean(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None and math.isfinite(v)]
    return float(sum(clean) / len(clean)) if clean else None


def _summarize_group(rows: list[dict[str, Any]], snapshot_date: pd.Timestamp) -> dict[str, Any]:
    evaluated = [_evaluate_row(r, snapshot_date) for r in rows]
    ok = [r for r in evaluated if r.get("eval_status") == "OK"]
    by_label: dict[str, list[dict[str, Any]]] = {}
    for r in ok:
        by_label.setdefault(str(r.get("label") or ""), []).append(r)
    return {
        "count": len(rows),
        "evaluated_count": len(ok),
        "avg_ret_1d": _mean([_as_float(r.get("ret_1d")) for r in ok]),
        "avg_ret_3d": _mean([_as_float(r.get("ret_3d")) for r in ok]),
        "avg_ret_5d": _mean([_as_float(r.get("ret_5d")) for r in ok]),
        "hit_5pct_5d_rate": _mean([1.0 if r.get("hit_5pct_5d") else 0.0 for r in ok]),
        "stop_3pct_first_5d_rate": _mean([1.0 if r.get("stop_3pct_first_5d") else 0.0 for r in ok]),
        "avg_max_drawdown": _mean([_as_float(r.get("max_drawdown_5d")) for r in ok]),
        "label_performance": {
            label: {
                "count": len(items),
                "avg_ret_5d": _mean([_as_float(r.get("ret_5d")) for r in items]),
                "avg_max_drawdown": _mean([_as_float(r.get("max_drawdown_5d")) for r in items]),
            }
            for label, items in sorted(by_label.items())
        },
    }


def main() -> None:
    args = _parse_args()
    out_dir = ROOT / args.output_root / args.date
    out_dir.mkdir(parents=True, exist_ok=True)
    snapshot_date = pd.Timestamp(datetime.strptime(args.date, "%Y%m%d").date())
    if not args.evaluate_only:
        _, rows = _run_daily_ranking(args)
        toplive, hot = _with_intraday(rows)
        _write_csv(out_dir / "legacy_candidates.csv", rows[:args.top_n])
        _write_csv(out_dir / "toplive_candidates.csv", toplive[:args.top_n])
        _write_csv(out_dir / "hot_candidates.csv", hot[:args.top_n])
    legacy = _read_csv(out_dir / "legacy_candidates.csv")
    toplive = _read_csv(out_dir / "toplive_candidates.csv")
    hot = _read_csv(out_dir / "hot_candidates.csv")
    sets = {
        "legacy": set(r.get("ticker", "") for r in legacy),
        "toplive": set(r.get("ticker", "") for r in toplive),
        "hot": set(r.get("ticker", "") for r in hot),
    }
    summary = {
        "date": args.date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "candidate_n": args.candidate_n,
        "top_n": args.top_n,
        "metrics": {
            "legacy": _summarize_group(legacy, snapshot_date),
            "toplive": _summarize_group(toplive, snapshot_date),
            "hot": _summarize_group(hot, snapshot_date),
        },
        "overlap": {
            "legacy_toplive": len(sets["legacy"] & sets["toplive"]),
            "legacy_hot": len(sets["legacy"] & sets["hot"]),
            "toplive_hot": len(sets["toplive"] & sets["hot"]),
        },
        "notes": "Forward shadow comparison only. Not a trading signal or recommendation.",
    }
    with (out_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"wrote {out_dir}")


if __name__ == "__main__":
    main()
