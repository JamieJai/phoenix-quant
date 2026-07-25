from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Any

import pandas as pd

from .intraday_features import INTRADAY_FEATURE_NAMES

BASE_INTRADAY_CACHE_COLUMNS = [
    "recorded_at",
    "ticker",
    "timestamp",
    "source",
    "label",
    "current_price",
    "previous_close",
]
INTRADAY_LABEL_COLUMNS = ["overlay_score", "forward_return_5m", "forward_return_10m", "outcome_5m", "outcome_10m"]
INTRADAY_CACHE_COLUMNS = BASE_INTRADAY_CACHE_COLUMNS + INTRADAY_LABEL_COLUMNS + INTRADAY_FEATURE_NAMES


def default_intraday_feature_cache_path(cache_dir: str = "data") -> str:
    return str(Path(cache_dir) / "intraday_features.csv")


def intraday_context_to_feature_row(ctx: Any, recorded_at: str | None = None) -> dict[str, object]:
    features = getattr(ctx, "features", {}) or {}
    row: dict[str, object] = {
        "recorded_at": recorded_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ticker": str(getattr(ctx, "ticker", "")).upper(),
        "timestamp": getattr(ctx, "timestamp", ""),
        "source": getattr(ctx, "source", ""),
        "label": getattr(ctx, "label", ""),
        "current_price": getattr(ctx, "current_price", None),
        "previous_close": getattr(ctx, "previous_close", None),
    }
    for name in INTRADAY_FEATURE_NAMES:
        row[name] = features.get(name)
    try:
        from .intraday_overlay_ranker import score_intraday_overlay_context
        row["overlay_score"] = score_intraday_overlay_context(ctx, 1).adjusted_score
    except Exception:
        row["overlay_score"] = pd.NA
    for name in INTRADAY_LABEL_COLUMNS[1:]:
        row[name] = pd.NA
    return row


def append_intraday_feature_rows(contexts: Iterable[Any], path: str | os.PathLike[str] | None = None) -> int:
    rows = [intraday_context_to_feature_row(ctx) for ctx in contexts]
    rows = [row for row in rows if row.get("ticker")]
    if not rows:
        return 0
    cache_path = Path(path or default_intraday_feature_cache_path())
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    for col in INTRADAY_CACHE_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    df = df[INTRADAY_CACHE_COLUMNS]
    write_header = not cache_path.exists() or cache_path.stat().st_size == 0
    df.to_csv(cache_path, mode="a", header=write_header, index=False, encoding="utf-8")
    return int(len(df))


def load_intraday_feature_cache(path: str | os.PathLike[str] | None = None) -> pd.DataFrame:
    cache_path = Path(path or default_intraday_feature_cache_path())
    if not cache_path.exists():
        return pd.DataFrame(columns=INTRADAY_CACHE_COLUMNS)
    df = pd.read_csv(cache_path)
    for col in INTRADAY_CACHE_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    return df[INTRADAY_CACHE_COLUMNS]
