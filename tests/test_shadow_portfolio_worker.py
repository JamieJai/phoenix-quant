"""Network-free bar availability tests for the shadow portfolio worker."""
from datetime import datetime, timezone

import pandas as pd

from scripts.phoenix_shadow_portfolio_worker import completed_bars


def test_worker_excludes_incomplete_one_minute_bar():
    frame = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [101.0, 102.0],
            "Low": [99.0, 100.0],
            "Close": [100.5, 101.5],
        },
        index=pd.to_datetime(
            ["2026-07-29T14:00:00Z", "2026-07-29T14:01:00Z"],
            utc=True,
        ),
    )
    rows = completed_bars(
        "AAPL",
        frame,
        now=datetime(2026, 7, 29, 14, 1, 30, tzinfo=timezone.utc),
    )
    assert len(rows) == 1
    assert rows[0].available_at == datetime(
        2026,
        7,
        29,
        14,
        1,
        tzinfo=timezone.utc,
    )
