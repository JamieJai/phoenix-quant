#!/usr/bin/env python3
"""Build forward-only paper regime evidence from point-in-time cached inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREREG = (
    "research/preregistrations/PAPER_MARKET_REGIME_EVIDENCE_V1.json"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(row: pd.Series, name: str) -> float | None:
    try:
        value = float(row.get(name))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _timestamp(raw: object) -> datetime | None:
    if raw is None or pd.isna(raw):
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        return None
    return value.astimezone(timezone.utc)


def classify(row: pd.Series, *, threshold_pct: float) -> dict[str, object]:
    timestamp = _timestamp(row.get("timestamp") or row.get("recorded_at"))
    stock = _number(row, "ret_fast_3bar_pct")
    rs_qqq = _number(row, "sector_rs_qqq_pct")
    rs_smh = _number(row, "sector_rs_smh_pct")
    rs_soxx = _number(row, "sector_rs_soxx_pct")
    values = (stock, rs_qqq, rs_smh, rs_soxx)
    if timestamp is None or any(value is None for value in values):
        return {
            "feature_available_at_utc": (
                timestamp.isoformat() if timestamp is not None else None
            ),
            "market_date": None,
            "qqq_return_10m_pct": None,
            "semi_return_10m_pct": None,
            "regime": "UNKNOWN",
            "point_in_time_valid": False,
        }
    qqq = stock - rs_qqq
    smh = stock - rs_smh
    soxx = stock - rs_soxx
    semi = (smh + soxx) / 2.0
    if qqq >= threshold_pct and semi >= threshold_pct:
        regime = "RISK_ON"
    elif qqq <= -threshold_pct and semi <= -threshold_pct:
        regime = "RISK_OFF"
    else:
        regime = "MIXED"
    return {
        "feature_available_at_utc": timestamp.isoformat(),
        "market_date": timestamp.astimezone(
            ZoneInfo("America/New_York")
        ).date().isoformat(),
        "qqq_return_10m_pct": qqq,
        "semi_return_10m_pct": semi,
        "regime": regime,
        "point_in_time_valid": True,
    }


def build(
    cache: str,
    preregistration: str = DEFAULT_PREREG,
) -> tuple[pd.DataFrame, dict[str, object]]:
    cache_path = ROOT / cache
    prereg_path = ROOT / preregistration
    frame = pd.read_csv(cache_path)
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    start = str(prereg["prospective_market_date_start"])
    threshold = 0.20
    minimum_per_regime = int(
        prereg["acceptance_gate"]["minimum_mature_5m_outcomes_per_regime"]
    )
    minimum_regimes = int(
        prereg["acceptance_gate"]["minimum_distinct_regimes"]
    )

    rows: list[dict[str, object]] = []
    for _, row in frame.iterrows():
        context = classify(row, threshold_pct=threshold)
        market_date = context["market_date"]
        forward = _number(row, "forward_return_5m")
        mature = forward is not None and abs(forward) <= 1.0
        prospective = bool(
            context["point_in_time_valid"]
            and market_date is not None
            and str(market_date) >= start
        )
        rows.append(
            {
                "ticker": str(row.get("ticker", "")).upper(),
                **context,
                "forward_return_5m": forward,
                "mature_5m": mature,
                "evidence_scope": (
                    "PROSPECTIVE"
                    if prospective
                    else "HISTORICAL_EXPLORATORY_ONLY"
                ),
                "eligible_for_gate": prospective and mature,
            }
        )
    evidence = pd.DataFrame(rows)
    qualifying_counts: dict[str, int] = {}
    eligible = evidence[
        evidence["eligible_for_gate"].eq(True)
        & ~evidence["regime"].eq("UNKNOWN")
    ]
    for regime, group in eligible.groupby("regime"):
        qualifying_counts[str(regime)] = int(len(group))
    qualifying_regimes = sorted(
        regime
        for regime, count in qualifying_counts.items()
        if count >= minimum_per_regime
    )
    passed = len(qualifying_regimes) >= minimum_regimes
    summary = {
        "status": "PASS" if passed else "COLLECTING",
        "evidence_type": "PAPER_MARKET_REGIME_EVIDENCE_V1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z"),
        "preregistration": {
            "artifact": preregistration,
            "sha256": sha256(prereg_path),
        },
        "prospective_market_date_start": start,
        "threshold_pct": threshold,
        "minimum_mature_per_regime": minimum_per_regime,
        "minimum_distinct_regimes": minimum_regimes,
        "rows": int(len(evidence)),
        "prospective_rows": int(
            evidence["evidence_scope"].eq("PROSPECTIVE").sum()
        ),
        "prospective_mature_rows": int(len(eligible)),
        "mature_counts_by_regime": qualifying_counts,
        "qualifying_regimes": qualifying_regimes,
        "qualifying_regime_count": len(qualifying_regimes),
        "historical_rows_used_for_gate": False,
        "production_score_changed": False,
        "champion_changed": False,
        "paper_signal_changed": False,
        "broker_routes_called": False,
        "live_enabled": False,
    }
    return evidence, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default="data/intraday_features.csv")
    parser.add_argument("--preregistration", default=DEFAULT_PREREG)
    parser.add_argument(
        "--output-dir",
        default="reports/paper_trading/regime_evidence",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    evidence, summary = build(args.cache, args.preregistration)
    output = ROOT / args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    rows_path = output / "paper_regime_evidence_latest.csv"
    summary_path = output / "paper_regime_evidence_latest.json"
    evidence.to_csv(rows_path, index=False, lineterminator="\n")
    summary["rows_artifact"] = {
        "artifact": str(rows_path.relative_to(ROOT)),
        "sha256": sha256(rows_path),
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    result = {
        **summary,
        "summary_artifact": str(summary_path.relative_to(ROOT)),
        "summary_sha256": sha256(summary_path),
    }
    print(
        json.dumps(result, ensure_ascii=False)
        if args.json
        else f"{summary['status']} regimes={summary['qualifying_regime_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
