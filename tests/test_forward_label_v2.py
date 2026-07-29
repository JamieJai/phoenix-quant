"""Network-free tests for source-isolated matured 5-minute labels."""
import math

import pandas as pd

from scripts.phoenix_intraday_label_cache import (
    _cached_observation_labels,
    label_from_bars,
)


def test_label_uses_first_bar_available_after_target():
    bars = pd.DataFrame(
        {"Close": [101.0, 102.0, 103.0]},
        index=pd.to_datetime(
            [
                "2026-07-29T13:30:00Z",
                "2026-07-29T13:35:00Z",
                "2026-07-29T13:40:00Z",
            ],
            utc=True,
        ),
    )
    result = label_from_bars(
        bars,
        signal_timestamp=pd.Timestamp("2026-07-29T13:35:00Z"),
        signal_price=100.0,
        horizon_minutes=5,
    )
    assert result is not None
    value, available_at = result
    assert math.isclose(value, 0.02)
    assert available_at == "2026-07-29T13:40:00+00:00"


def test_missing_target_window_is_not_imputed():
    bars = pd.DataFrame(
        {"Close": [101.0]},
        index=pd.to_datetime(["2026-07-29T15:00:00Z"], utc=True),
    )
    result = label_from_bars(
        bars,
        signal_timestamp=pd.Timestamp("2026-07-29T13:35:00Z"),
        signal_price=100.0,
        horizon_minutes=5,
    )
    assert result is None


def test_prospective_rows_are_not_labeled_from_next_cached_observation():
    frame = pd.DataFrame(
        {
            "ticker": ["AAPL", "AAPL"],
            "timestamp": [
                "2026-07-29T13:35:00Z",
                "2026-07-29T13:45:00Z",
            ],
            "current_price": [100.0, 102.0],
            "forward_return_5m": [pd.NA, pd.NA],
            "forward_return_10m": [pd.NA, pd.NA],
            "outcome_5m": [pd.NA, pd.NA],
            "outcome_10m": [pd.NA, pd.NA],
        }
    )
    filled = _cached_observation_labels(
        frame,
        prospective_start="2026-07-29",
    )
    assert filled == 0
    assert frame["forward_return_5m"].isna().all()
