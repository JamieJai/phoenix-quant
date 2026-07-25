#!/usr/bin/env python3
"""Research-only intraday base versus bounded-overlay comparison.

The feature cache is deliberately treated as an observation table.  This
script never writes model artifacts or changes production configuration.  It
returns BLOCKED until point-in-time forward labels are present in the cache.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


LABEL_CANDIDATES = ("forward_return_5m", "forward_return_10m", "tp_hit", "outcome", "label")
BASE_CANDIDATES = ("opportunity_score", "intraday_score", "score")
OVERLAY_CANDIDATES = ("overlay_score", "adjusted_score", "intraday_overlay_score")


def _pick(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    for name in candidates:
        if name in columns:
            return name
    return None


def _payload(status: str, **kwargs: Any) -> dict[str, Any]:
    return {"status": status, **kwargs}


def run(path: str) -> dict[str, Any]:
    cache = Path(path)
    if not cache.exists():
        return _payload("BLOCKED", path=str(cache), missing_requirements=[f"create {cache} with point-in-time intraday feature rows"])
    try:
        frame = pd.read_csv(cache)
    except Exception as exc:
        return _payload("BLOCKED", path=str(cache), missing_requirements=[f"readable CSV ({exc})"])
    cols = [str(c) for c in frame.columns]
    label = _pick(cols, LABEL_CANDIDATES)
    base = _pick(cols, BASE_CANDIDATES)
    overlay = _pick(cols, OVERLAY_CANDIDATES)
    missing: list[str] = []
    if "timestamp" not in cols:
        missing.append("UTC timestamp column")
    if "ticker" not in cols:
        missing.append("ticker column")
    if label is None:
        missing.append("forward outcome label (e.g. forward_return_5m/10m or tp_hit), computed only after horizon matures")
    if base is None:
        missing.append("base opportunity/intraday score column")
    if overlay is None:
        missing.append("overlay score column; record overlay score alongside each observation")
    if missing:
        return _payload("BLOCKED", path=str(cache), rows=int(len(frame)), columns=cols, missing_requirements=missing,
                        note="No ablation or promotion decision is made without matured point-in-time labels.")

    work = frame[[label, base, overlay]].copy().apply(pd.to_numeric, errors="coerce").dropna()
    work = work[work[label].abs() <= 1.0]
    if work.empty:
        return _payload("BLOCKED", path=str(cache), rows=int(len(frame)), missing_requirements=["non-empty matured label/score rows"])
    y = work[label]
    # Explicit hit labels are used as-is; numeric forward returns use > 0.
    hit = y if set(y.dropna().unique()).issubset({0, 1}) else (y > 0).astype(float)
    def stats(score: pd.Series) -> dict[str, float]:
        q = score >= score.quantile(0.75)
        return {"n": float(q.sum()), "hit_rate": float(hit[q].mean()) if q.any() else float("nan"), "mean_label": float(y[q].mean()) if q.any() else float("nan")}
    return _payload("READY", path=str(cache), rows=int(len(frame)), labeled_rows=int(len(work)), label_column=label,
                    base_column=base, overlay_column=overlay, base=stats(work[base]), overlay=stats(work[overlay]),
                    delta_hit_rate=float(stats(work[overlay])["hit_rate"] - stats(work[base])["hit_rate"]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="data/intraday_features.csv")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    result = run(args.path)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
