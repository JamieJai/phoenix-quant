"""Prospective, research-only semiconductor damage/rebound overlay.

The formula and constants are frozen by
``research/preregistrations/SEMI_DAMAGE_REBOUND_OVERLAY_V1.json``.  This module
is deliberately not imported by production ranking or Telegram paths.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Optional


HYPOTHESIS_ID = "SEMI_DAMAGE_REBOUND_OVERLAY_V1"
DAMAGE_SCALE = 0.10
MAX_PENALTY_POINTS = -5.0
MISSING_CONFIDENCE_DOWNGRADE_POINTS = -10.0


@dataclass(frozen=True)
class SemiDamageReboundInput:
    as_of_close_utc: datetime
    available_from_utc: datetime
    prediction_timestamp_utc: datetime
    qqq_return_20d: Optional[float]
    smh_return_20d: Optional[float]
    soxx_return_20d: Optional[float]
    smh_return_5d: Optional[float]
    soxx_return_5d: Optional[float]


@dataclass(frozen=True)
class SemiDamageReboundResult:
    hypothesis_id: str
    status: str
    semi_20d: Optional[float]
    damage: Optional[float]
    rebound: Optional[int]
    penalty_points: float
    confidence_adjustment_points: float
    available_from_utc: str
    research_only: bool = True
    runtime_enabled: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _require_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware UTC")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{name} must use a UTC offset")
    return value.astimezone(timezone.utc)


def _finite_or_none(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    number = float(value)
    return number if isfinite(number) else None


def evaluate_semi_damage_rebound(
    values: SemiDamageReboundInput,
) -> SemiDamageReboundResult:
    """Evaluate the frozen formula only at or after the next-session boundary."""

    as_of = _require_utc(values.as_of_close_utc, "as_of_close_utc")
    available = _require_utc(values.available_from_utc, "available_from_utc")
    prediction = _require_utc(
        values.prediction_timestamp_utc, "prediction_timestamp_utc"
    )
    if available <= as_of:
        raise ValueError("available_from_utc must be after the source close")
    if prediction < available:
        raise ValueError("same-session or pre-availability application is forbidden")

    raw = (
        values.qqq_return_20d,
        values.smh_return_20d,
        values.soxx_return_20d,
        values.smh_return_5d,
        values.soxx_return_5d,
    )
    clean = tuple(_finite_or_none(item) for item in raw)
    if any(item is None for item in clean):
        return SemiDamageReboundResult(
            hypothesis_id=HYPOTHESIS_ID,
            status="MISSING_DATA_NEUTRAL_PENALTY",
            semi_20d=None,
            damage=None,
            rebound=None,
            penalty_points=0.0,
            confidence_adjustment_points=MISSING_CONFIDENCE_DOWNGRADE_POINTS,
            available_from_utc=available.isoformat(),
        )

    qqq20, smh20, soxx20, smh5, soxx5 = clean
    assert qqq20 is not None
    assert smh20 is not None
    assert soxx20 is not None
    assert smh5 is not None
    assert soxx5 is not None
    semi_20d = (smh20 + soxx20) / 2.0
    damage = max(0.0, min(1.0, (qqq20 - semi_20d) / DAMAGE_SCALE))
    rebound = int((smh5 + soxx5) / 2.0 > 0.0)
    penalty = MAX_PENALTY_POINTS * damage * rebound
    return SemiDamageReboundResult(
        hypothesis_id=HYPOTHESIS_ID,
        status="FORWARD_RESEARCH_READY",
        semi_20d=semi_20d,
        damage=damage,
        rebound=rebound,
        penalty_points=penalty,
        confidence_adjustment_points=0.0,
        available_from_utc=available.isoformat(),
    )
