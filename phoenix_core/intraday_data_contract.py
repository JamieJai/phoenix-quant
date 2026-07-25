"""Research-only schemas for the planned intraday data layer.

These contracts deliberately contain no provider or scoring logic.  Providers may
map their payloads into these point-in-time records before feature calculation.
All timestamps must be timezone-aware UTC ISO-8601 strings.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass(frozen=True)
class IntradayBar:
    """One OHLCV bar available at ``timestamp`` (no future fields)."""
    timestamp: str
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    volume: Optional[float]
    trade_value: Optional[float] = None
    timeframe: str = "5m"


@dataclass(frozen=True)
class IntradayMarketSnapshot:
    """Point-in-time stock/market context consumed by research features."""
    ticker: str
    timestamp: str
    bars: tuple[IntradayBar, ...] = ()
    current_price: Optional[float] = None
    previous_close: Optional[float] = None
    day_open: Optional[float] = None
    day_high: Optional[float] = None
    day_low: Optional[float] = None
    cumulative_volume: Optional[float] = None
    vwap: Optional[float] = None
    atr_intraday: Optional[float] = None
    market_ticker: Optional[str] = None
    sector_ticker: Optional[str] = None
    market_return_pct: Optional[float] = None
    sector_return_pct: Optional[float] = None
    rvol_tod: Optional[float] = None
    source: str = "unknown"
    freshness_seconds: Optional[float] = None


@dataclass(frozen=True)
class EventRiskSnapshot:
    """Known event fields; unknown values remain None (never guessed)."""
    timestamp: str
    earnings_days: Optional[int] = None
    earnings_session: Optional[str] = None
    dividend_ex_date: Optional[str] = None
    corporate_action: Optional[str] = None
    catalyst_tags: tuple[str, ...] = ()
    source: str = "unknown"


@dataclass(frozen=True)
class KeyLevelSnapshot:
    price: float
    strength: float = 0.0
    sources: tuple[str, ...] = ()
    level_type: str = "unknown"


def contract_to_dict(value: Any) -> dict[str, Any]:
    """Serialize a contract for snapshot storage without provider coupling."""
    return asdict(value)


def validate_timestamp(timestamp: str) -> bool:
    """Require an explicit UTC designator for point-in-time records."""
    return isinstance(timestamp, str) and (timestamp.endswith("Z") or timestamp.endswith("+00:00"))
