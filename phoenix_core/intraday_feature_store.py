from __future__ import annotations

import csv
import fcntl
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


def _existing_header(cache_path: Path) -> list[str]:
    if not cache_path.exists() or cache_path.stat().st_size == 0:
        return []
    with cache_path.open(newline="", encoding="utf-8") as handle:
        return next(csv.reader(handle), [])


def _ensure_schema_unlocked(cache_path: Path) -> dict[str, object]:
    header = _existing_header(cache_path)
    if not header:
        return {
            "status": "EMPTY",
            "migrated": False,
            "rows": 0,
            "columns": list(INTRADAY_CACHE_COLUMNS),
        }
    unknown = [column for column in header if column not in INTRADAY_CACHE_COLUMNS]
    target = [*INTRADAY_CACHE_COLUMNS, *unknown]
    if header == target:
        return {
            "status": "CURRENT",
            "migrated": False,
            "rows": None,
            "columns": target,
        }
    frame = pd.read_csv(cache_path)
    for column in target:
        if column not in frame.columns:
            frame[column] = pd.NA
    frame = frame[target]
    temporary = cache_path.with_name(f".{cache_path.name}.schema-migrate.tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8", lineterminator="\n")
    os.replace(temporary, cache_path)
    return {
        "status": "MIGRATED",
        "migrated": True,
        "rows": int(len(frame)),
        "columns": target,
        "previous_columns": header,
    }


def ensure_intraday_feature_cache_schema(
    path: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    cache_path = Path(path or default_intraday_feature_cache_path())
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = cache_path.with_suffix(cache_path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _ensure_schema_unlocked(cache_path)


def append_intraday_feature_rows(
    contexts: Iterable[Any],
    path: str | os.PathLike[str] | None = None,
    *,
    dedupe_keys: tuple[str, ...] | None = None,
) -> int:
    rows = [intraday_context_to_feature_row(ctx) for ctx in contexts]
    rows = [row for row in rows if row.get("ticker")]
    if not rows:
        return 0
    cache_path = Path(path or default_intraday_feature_cache_path())
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = cache_path.with_suffix(cache_path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        schema = _ensure_schema_unlocked(cache_path)
        target_columns = list(schema["columns"])
        df = pd.DataFrame(rows)
        if dedupe_keys and cache_path.exists() and cache_path.stat().st_size:
            valid_keys = [
                key
                for key in dedupe_keys
                if key in df.columns and key in target_columns
            ]
            if valid_keys:
                existing = pd.read_csv(
                    cache_path,
                    usecols=valid_keys,
                    dtype=str,
                ).fillna("")
                existing_keys = set(
                    map(tuple, existing[valid_keys].astype(str).to_numpy())
                )
                candidate_keys = df[valid_keys].fillna("").astype(str)
                keep = [
                    tuple(values) not in existing_keys
                    for values in candidate_keys.to_numpy()
                ]
                df = df.loc[keep].copy()
                if df.empty:
                    return 0
        for col in target_columns:
            if col not in df.columns:
                df[col] = pd.NA
        df = df[target_columns]
        write_header = not cache_path.exists() or cache_path.stat().st_size == 0
        df.to_csv(
            cache_path,
            mode="a",
            header=write_header,
            index=False,
            encoding="utf-8",
            lineterminator="\n",
        )
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
