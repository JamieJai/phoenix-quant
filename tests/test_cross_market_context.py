"""Focused safety tests for the experimental cross-market context feature."""
from datetime import date

import numpy as np
import pandas as pd

from phoenix_core.engines.cross_market_context_engine import CrossMarketContextEngine
from phoenix_core.models import CrossMarketContextInput


def _frame(values, start="2024-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="D")
    return pd.DataFrame({"Close": values}, index=idx)


def _run(data, as_of="2024-01-10"):
    return CrossMarketContextEngine().run(
        CrossMarketContextInput(ticker="SMH", as_of=date.fromisoformat(as_of), ohlcv=data, sector_symbol="SOXX")
    )


def _payload(result):
    """Extract numeric payload without coupling tests to presentation fields."""
    if hasattr(result, "values"):
        return result.values
    if hasattr(result, "features"):
        return result.features
    return {k: v for k, v in vars(result).items() if isinstance(v, (int, float))}


def test_cross_market_context_is_point_in_time():
    base = {"SPY": _frame(np.arange(1.0, 21.0)), "QQQ": _frame(np.arange(2.0, 22.0)), "SOXX": _frame(np.arange(3.0, 23.0))}
    changed = {k: v.copy() for k, v in base.items()}
    changed["SPY"].loc[pd.Timestamp("2024-01-11"):, "Close"] = 10_000.0
    a, b = _payload(_run(base)), _payload(_run(changed))
    assert a.keys() == b.keys()
    for key in a:
        assert np.isclose(float(a[key]), float(b[key]), equal_nan=True), key


def test_cross_market_context_missing_inputs_are_safe():
    result = _run({"SPY": _frame(np.arange(1.0, 5.0))})
    for value in _payload(result).values():
        assert np.isfinite(float(value))


def test_cross_market_context_outputs_are_bounded():
    result = _run({k: _frame(np.linspace(1, 100, 30)) for k in ("SPY", "QQQ", "SOXX")})
    for key, value in _payload(result).items():
        if "score" in key.lower() or "spread" in key.lower() or "context" in key.lower():
            assert -100.0 <= float(value) <= 100.0, (key, value)
