#!/usr/bin/env python3
"""Research-only comparison of Phoenix OOS metrics against random baselines.

Selects the newest benchmark pair under ``models/current`` (or a supplied
root), and reports maturity of the evaluation window.  It never alters model
artifacts or promotion state.
"""
from __future__ import annotations
import argparse, csv, json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

def newest_pair(root: Path):
    pairs=[]
    for s in root.rglob("benchmark_summary.csv"):
        b=s.parent / "benchmark_random_baseline.csv"
        if b.exists(): pairs.append((s.stat().st_mtime, s, b))
    return max(pairs, default=None)

def row(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return next(csv.DictReader(f), {})

def num(v):
    try: return float(v)
    except (TypeError, ValueError): return None

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--models-root", default="models/current")
    ap.add_argument("--as-of", help="UTC date YYYY-MM-DD (default today)")
    ap.add_argument("--maturity-days", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    args=ap.parse_args()
    pair=newest_pair(Path(args.models_root))
    if not pair: raise SystemExit("no benchmark summary/random baseline pair found")
    _, summary_path, random_path=pair
    s,r=row(summary_path),row(random_path)
    asof=date.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc).date()
    end=date.fromisoformat(s["end"]) if s.get("end") else None
    mature=bool(end and end + timedelta(days=args.maturity_days) <= asof)
    metrics={
      "hit_5pct_5d_rate": ("hit_5pct_5d_rate","random_hit_5pct_5d_mean"),
      "hit_10pct_10d_rate": ("hit_10pct_10d_rate","random_hit_10pct_10d_mean"),
      "avg_fwd_max_ret_5d": ("avg_fwd_max_ret_5d","random_avg_fwd5_mean"),
      "avg_fwd_max_ret_10d": ("avg_fwd_max_ret_10d","random_avg_fwd10_mean"),
    }
    comparison={}
    for name,(a,b) in metrics.items():
        model,baseline=num(s.get(a)),num(r.get(b)); comparison[name]={"model":model,"random":baseline,"delta":(model-baseline if model is not None and baseline is not None else None)}
    out={"status":"MATURE" if mature else "IMMATURE","as_of":asof.isoformat(),"maturity_days":args.maturity_days,"window":{"start":s.get("start"),"end":s.get("end")},"summary_path":str(summary_path),"random_path":str(random_path),"n_dates":s.get("n_dates"),"n_trades":s.get("n_trades"),"comparison":comparison}
    print(json.dumps(out,ensure_ascii=False,indent=2) if args.json else "\n".join([f"{k}: model={v['model']:.4f} random={v['random']:.4f} delta={v['delta']:+.4f}" for k,v in comparison.items()]))

if __name__ == "__main__": main()
