#!/usr/bin/env python3
"""Audit predicted versus realized paper outcomes without retuning parameters."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from statistics import mean
from typing import Optional


PREDICTION_COLUMNS = (
    "predicted_return",
    "predicted_return_5m",
    "model_expected_return",
)
GROSS_COLUMNS = ("forward_return_5m", "realized_gross_return")
ACTUAL_SLIPPAGE_COLUMNS = (
    "actual_fill_slippage_bps",
    "paper_fill_slippage_bps",
)


def _number(row: dict[str, str], columns: tuple[str, ...]) -> Optional[float]:
    for column in columns:
        try:
            value = float(row.get(column, ""))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            return value
    return None


def _average(values: list[float]) -> Optional[float]:
    return mean(values) if values else None


def audit(
    path: str,
    *,
    commission_bps: float = 2.0,
    estimated_slippage_bps: float = 5.0,
) -> dict[str, object]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    predictions: list[float] = []
    gross_returns: list[float] = []
    net_returns: list[float] = []
    actual_slippage: list[float] = []
    invalid_realized_returns = 0
    roundtrip_cost = 2.0 * (commission_bps + estimated_slippage_bps) / 10_000.0
    for row in rows:
        predicted = _number(row, PREDICTION_COLUMNS)
        gross = _number(row, GROSS_COLUMNS)
        actual = _number(row, ACTUAL_SLIPPAGE_COLUMNS)
        if predicted is not None:
            predictions.append(predicted)
        if gross is not None:
            if abs(gross) <= 1.0:
                gross_returns.append(gross)
                net_returns.append(gross - roundtrip_cost)
            else:
                invalid_realized_returns += 1
        if actual is not None:
            actual_slippage.append(actual)
    complete = bool(predictions and gross_returns and actual_slippage)
    return {
        "status": "CALIBRATION_READY" if complete else "CALIBRATION_INCOMPLETE",
        "parameter_retuning_allowed": False,
        "rows": len(rows),
        "coverage": {
            "predicted_return": len(predictions),
            "realized_gross_return": len(gross_returns),
            "realized_net_return": len(net_returns),
            "actual_or_paper_fill_slippage": len(actual_slippage),
            "invalid_realized_return_excluded": invalid_realized_returns,
        },
        "means": {
            "predicted_return": _average(predictions),
            "realized_gross_return": _average(gross_returns),
            "realized_net_return": _average(net_returns),
            "estimated_one_way_slippage_bps": estimated_slippage_bps,
            "actual_or_paper_fill_slippage_bps": _average(actual_slippage),
        },
        "roundtrip_cost_fraction": roundtrip_cost,
        "blocking_reasons": [
            name
            for name, count in {
                "predicted_return_missing": len(predictions),
                "realized_return_missing": len(gross_returns),
                "actual_or_paper_fill_slippage_missing": len(actual_slippage),
            }.items()
            if count == 0
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default="data/intraday_features.csv")
    parser.add_argument("--commission-bps", type=float, default=2.0)
    parser.add_argument("--estimated-slippage-bps", type=float, default=5.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = audit(
        args.path,
        commission_bps=args.commission_bps,
        estimated_slippage_bps=args.estimated_slippage_bps,
    )
    print(json.dumps(result, ensure_ascii=False) if args.json else result)


if __name__ == "__main__":
    main()
