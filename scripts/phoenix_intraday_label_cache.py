#!/usr/bin/env python3
"""Fill matured forward labels in the research intraday feature cache."""
from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd

def update(path: str) -> dict[str, int]:
    p = Path(path)
    if not p.exists(): return {"rows": 0, "labeled_5m": 0, "labeled_10m": 0}
    df = pd.read_csv(p)
    for c in ("forward_return_5m", "forward_return_10m", "outcome_5m", "outcome_10m"):
        if c not in df: df[c] = pd.NA
    ts = pd.to_datetime(df.get("timestamp"), utc=True, errors="coerce")
    px = pd.to_numeric(df.get("current_price"), errors="coerce")
    for ticker, idx in df.groupby(df["ticker"].astype(str).str.upper()).groups.items():
        order = sorted(idx, key=lambda i: ts.iloc[i] if pd.notna(ts.iloc[i]) else pd.Timestamp.max.tz_localize("UTC"))
        for i in order:
            if pd.isna(ts.iloc[i]) or pd.isna(px.iloc[i]) or px.iloc[i] == 0: continue
            for mins, ret, out in ((5, "forward_return_5m", "outcome_5m"), (10, "forward_return_10m", "outcome_10m")):
                target = ts.iloc[i] + pd.Timedelta(minutes=mins)
                future = next((j for j in order if pd.notna(ts.iloc[j]) and target <= ts.iloc[j] <= target + pd.Timedelta(minutes=30) and pd.notna(px.iloc[j])), None)
                if future is not None:
                    value = float(px.iloc[future] / px.iloc[i] - 1.0)
                    df.at[i, ret] = value; df.at[i, out] = int(value > 0)
    df.to_csv(p, index=False)
    return {"rows": len(df), "labeled_5m": int(df["forward_return_5m"].notna().sum()), "labeled_10m": int(df["forward_return_10m"].notna().sum())}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--path", default="data/intraday_features.csv"); args = ap.parse_args()
    print(update(args.path))
