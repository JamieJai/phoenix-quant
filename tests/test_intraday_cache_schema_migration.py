"""Atomic schema migration tests for the append-only intraday cache."""
import csv
from pathlib import Path

from phoenix_core.engines.intraday_context_engine import IntradayContext
from phoenix_core.intraday_feature_store import (
    INTRADAY_CACHE_COLUMNS,
    append_intraday_feature_rows,
    ensure_intraday_feature_cache_schema,
)


def test_old_header_is_migrated_before_append(tmp_path: Path):
    cache = tmp_path / "intraday.csv"
    old_columns = [
        "recorded_at",
        "ticker",
        "timestamp",
        "source",
        "label",
        "current_price",
        "previous_close",
    ]
    with cache.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=old_columns)
        writer.writeheader()
        writer.writerow(
            {
                "recorded_at": "2026-07-28T14:00:00+00:00",
                "ticker": "OLD",
                "timestamp": "2026-07-28T14:00:00+00:00",
                "source": "synthetic",
                "label": "OLD",
                "current_price": "100",
                "previous_close": "99",
            }
        )
    result = ensure_intraday_feature_cache_schema(cache)
    assert result["status"] == "MIGRATED"

    context = IntradayContext(
        ticker="NEW",
        timestamp="2026-07-29T14:00:00+00:00",
        source="synthetic",
        current_price=101.0,
        previous_close=100.0,
        current_vs_prev_close_pct=1.0,
        day_open=100.0,
        intraday_return_pct=1.0,
        latest_10m_return_pct=0.5,
        latest_30m_return_pct=0.3,
        today_volume=1_000.0,
        avg_intraday_volume=900.0,
        intraday_volume_ratio=1.1,
        vwap=100.5,
        vwap_position_pct=0.5,
        above_vwap=True,
        intraday_high=102.0,
        pullback_from_intraday_high_pct=-1.0,
        intraday_score=70,
        intraday_risk_score=30,
        label="TEST",
        notes=[],
    )
    assert append_intraday_feature_rows([context], cache) == 1
    with cache.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert list(rows[0]) == INTRADAY_CACHE_COLUMNS
    assert rows[0]["ticker"] == "OLD"
    assert rows[1]["ticker"] == "NEW"
