"""Network-free tests for paper sampling timestamps and PIT universe snapshots."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from phoenix_core.engines.intraday_context_engine import IntradayContextEngine
from phoenix_core.intraday_feature_store import append_intraday_feature_rows
from scripts.phoenix_intraday_shadow_collector import _parse_timestamp


def test_intraday_engine_uses_latest_bar_timestamp():
    engine = IntradayContextEngine()
    frame = pd.DataFrame(
        {"Close": [100.0, 101.0]},
        index=pd.to_datetime(
            ["2026-07-29T13:30:00Z", "2026-07-29T13:40:00Z"],
            utc=True,
        ),
    )
    timestamp = engine._latest_data_timestamp((frame, "10m"))
    assert timestamp == "2026-07-29T13:50:00+00:00"


def test_daily_frame_does_not_mask_current_price_intraday_availability():
    engine = IntradayContextEngine()
    intraday = pd.DataFrame(
        {"Close": [100.0]},
        index=pd.to_datetime(["2026-07-29T13:40:00Z"], utc=True),
    )
    daily = pd.DataFrame(
        {"Close": [99.0]},
        index=pd.to_datetime(["2026-07-29T04:00:00Z"], utc=True),
    )
    current_10m = float(intraday["Close"].iloc[-1])
    if current_10m is not None:
        timestamp = engine._latest_data_timestamp((intraday, "10m"))
    else:
        timestamp = engine._latest_data_timestamp((daily, "1d"))
    assert timestamp == "2026-07-29T13:50:00+00:00"


def test_research_engine_excludes_incomplete_intraday_bar():
    engine = IntradayContextEngine(completed_bars_only=True)
    frame = pd.DataFrame(
        {"Close": [100.0, 101.0]},
        index=pd.to_datetime(
            ["2026-07-29T12:00:00Z", "2026-07-29T12:10:00Z"],
            utc=True,
        ),
    )
    completed = engine._completed_bars(
        frame,
        "10m",
        pd.Timestamp("2026-07-29T12:15:00Z"),
    )
    assert list(completed["Close"]) == [100.0]


def test_shadow_append_deduplicates_ticker_timestamp_source(tmp_path):
    context = SimpleNamespace(
        ticker="AAPL",
        timestamp="2026-07-29T12:10:00+00:00",
        source="yfinance",
        label="MIXED_INTRADAY_CONTEXT",
        current_price=100.0,
        previous_close=99.0,
        features={},
    )
    cache = tmp_path / "intraday.csv"
    first = append_intraday_feature_rows(
        [context],
        cache,
        dedupe_keys=("ticker", "timestamp", "source"),
    )
    second = append_intraday_feature_rows(
        [context],
        cache,
        dedupe_keys=("ticker", "timestamp", "source"),
    )
    assert first == 1
    assert second == 0
    assert len(pd.read_csv(cache)) == 1


def test_naive_shadow_timestamp_fails_closed():
    assert _parse_timestamp("2026-07-29T13:40:00") is None
    parsed = _parse_timestamp("2026-07-29T13:40:00Z")
    assert parsed == datetime(2026, 7, 29, 13, 40, tzinfo=timezone.utc)
