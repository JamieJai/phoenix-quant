from __future__ import annotations

from typing import Dict, Sequence

import numpy as np
import pandas as pd


def compute_forward_labels(df: pd.DataFrame, horizons: Sequence[int] = (5, 10),
                           thresholds: Sequence[float] = (0.05, 0.10)) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    close = df["Close"]
    high = df["High"]
    for h, th in zip(horizons, thresholds):
        future_max_high = high.shift(-1).iloc[::-1].rolling(window=h, min_periods=h).max().iloc[::-1]
        max_ret = (future_max_high - close) / close
        out[f"fwd_max_ret_{h}d"] = max_ret
        hit = pd.Series(np.where(max_ret >= th, 1.0, 0.0), index=max_ret.index)
        hit[max_ret.isna()] = np.nan
        out[f"hit_{int(th * 100)}pct_{h}d"] = hit
    return out


def row_to_label_dict(labels_df: pd.DataFrame, idx) -> Dict[str, float]:
    row = labels_df.loc[idx]
    if row.isna().any():
        return {}
    return {str(k): float(v) for k, v in row.items()}
