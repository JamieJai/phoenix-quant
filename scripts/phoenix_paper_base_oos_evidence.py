#!/usr/bin/env python3
"""Forward-only base-versus-overlay paper non-degradation evidence."""
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
    "research/preregistrations/PAPER_BASE_OOS_NON_DEGRADATION_V1.json"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _market_date(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        return None
    return timestamp.astimezone(ZoneInfo("America/New_York")).date().isoformat()


def _selected(frame: pd.DataFrame, score: str) -> pd.DataFrame:
    selections = []
    for _, group in frame.groupby("market_date", sort=True):
        count = max(1, int(math.ceil(len(group) * 0.25)))
        selections.append(
            group.sort_values(
                [score, "ticker", "timestamp"],
                ascending=[False, True, True],
            ).head(count)
        )
    return pd.concat(selections, ignore_index=True) if selections else frame.head(0)


def evaluate(frame: pd.DataFrame, prereg: dict) -> dict[str, object]:
    work = frame.copy()
    work["market_date"] = work["timestamp"].map(_market_date)
    for column in ("intraday_score", "overlay_score", "forward_return_5m"):
        work[column] = pd.to_numeric(work[column], errors="coerce")
    work = work.dropna(
        subset=[
            "market_date",
            "ticker",
            "timestamp",
            "intraday_score",
            "overlay_score",
            "forward_return_5m",
        ]
    )
    work = work[work["forward_return_5m"].abs() <= 1.0]
    start = str(prereg["prospective_market_date_start"])
    work = work[work["market_date"] >= start]
    base = _selected(work, "intraday_score")
    overlay = _selected(work, "overlay_score")
    cost = float(prereg["selection"]["cost_fraction_roundtrip"])

    def metrics(selected: pd.DataFrame) -> dict[str, float | int | None]:
        if selected.empty:
            return {"n": 0, "hit_rate": None, "net_mean": None}
        gross = selected["forward_return_5m"]
        return {
            "n": int(len(selected)),
            "hit_rate": float((gross > 0).mean()),
            "net_mean": float((gross - cost).mean()),
        }

    base_metrics = metrics(base)
    overlay_metrics = metrics(overlay)
    minimum = prereg["minimum_evidence"]
    dates = int(work["market_date"].nunique())
    enough = (
        len(work) >= int(minimum["prospective_mature_rows"])
        and dates >= int(minimum["prospective_market_dates"])
        and int(base_metrics["n"]) >= int(minimum["base_selected_rows"])
        and int(overlay_metrics["n"]) >= int(minimum["overlay_selected_rows"])
    )
    delta_net = None
    delta_hit = None
    if base_metrics["net_mean"] is not None and overlay_metrics["net_mean"] is not None:
        delta_net = float(overlay_metrics["net_mean"] - base_metrics["net_mean"])
    if base_metrics["hit_rate"] is not None and overlay_metrics["hit_rate"] is not None:
        delta_hit = float(
            overlay_metrics["hit_rate"] - base_metrics["hit_rate"]
        )
    gate = prereg["acceptance_gate"]
    passed = bool(
        enough
        and delta_net is not None
        and delta_hit is not None
        and delta_net >= float(gate["overlay_minus_base_net_mean_min"])
        and delta_hit >= float(gate["overlay_minus_base_hit_rate_min"])
    )
    status = "COLLECTING" if not enough else ("PASS" if passed else "FAIL")
    return {
        "status": status,
        "evidence_type": "PAPER_BASE_OOS_NON_DEGRADATION_V1",
        "prospective_market_date_start": start,
        "prospective_mature_rows": int(len(work)),
        "prospective_market_dates": dates,
        "base": base_metrics,
        "overlay": overlay_metrics,
        "overlay_minus_base_net_mean": delta_net,
        "overlay_minus_base_hit_rate": delta_hit,
        "minimum_evidence_met": enough,
        "historical_rows_used_for_gate": False,
        "production_score_changed": False,
        "champion_changed": False,
        "paper_signal_changed": False,
        "broker_routes_called": False,
        "live_enabled": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default="data/intraday_features.csv")
    parser.add_argument("--preregistration", default=DEFAULT_PREREG)
    parser.add_argument(
        "--output",
        default=(
            "reports/paper_trading/base_oos/"
            "paper_base_oos_non_degradation_latest.json"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    prereg_path = ROOT / args.preregistration
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    frame = pd.read_csv(ROOT / args.cache)
    result = evaluate(frame, prereg)
    result["generated_at_utc"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    result["preregistration"] = {
        "artifact": args.preregistration,
        "sha256": sha256(prereg_path),
    }
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    payload = {
        **result,
        "artifact": str(output.relative_to(ROOT)),
        "artifact_sha256": sha256(output),
    }
    print(
        json.dumps(payload, ensure_ascii=False)
        if args.json
        else f"{result['status']} rows={result['prospective_mature_rows']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
