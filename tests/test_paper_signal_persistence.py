"""Network-free tests for paper fill provenance persistence."""
import csv
import json
import math
from pathlib import Path

from scripts.phoenix_paper_signal_runner import persist, run


def test_paper_fill_persists_signal_price_and_slippage(tmp_path: Path):
    source = tmp_path / "intraday.csv"
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "ticker",
                "timestamp",
                "current_price",
                "data_confidence_score",
                "rr_ratio",
                "forward_return_5m",
                "source",
                "label",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "ticker": "TEST",
                "timestamp": "2026-07-28T14:00:00+00:00",
                "current_price": "100",
                "data_confidence_score": "80",
                "rr_ratio": "2",
                "forward_return_5m": "0.01",
                "source": "synthetic",
                "label": "TEST_ONLY",
            }
        )

    result = run(str(source), replay=True)
    fills_csv = tmp_path / "fills.csv"
    artifacts = persist(result, fills_csv=str(fills_csv))

    assert result["accepted"] == 1
    assert result["live_broker"] is False
    assert result["broker_routes_called"] is False
    assert artifacts["fills_csv"]["sha256"]
    with fills_csv.open(newline="", encoding="utf-8") as handle:
        fill = next(csv.DictReader(handle))
    assert float(fill["signal_price"]) == 100.0
    assert math.isclose(float(fill["paper_fill_slippage_bps"]), 5.0, abs_tol=1e-9)
    assert float(fill["forward_return_5m"]) == 0.01
    assert fill["predicted_return"] == ""


def test_frozen_calibrator_applies_only_to_prospective_fill(tmp_path: Path):
    source = tmp_path / "intraday.csv"
    source.write_text(
        "ticker,timestamp,current_price,data_confidence_score,rr_ratio,source,label\n"
        "TEST,2026-07-29T14:00:00+00:00,100,80,2,yfinance,TEST_ONLY\n",
        encoding="utf-8",
    )
    calibrator = tmp_path / "calibrator.json"
    calibrator.write_text(
        json.dumps(
            {
                "status": "FROZEN",
                "model_type": "CONSTANT_HISTORICAL_MEAN_GROSS_RETURN",
                "calibrator_id": "PAPER_EXPECTED_RETURN_CALIBRATOR_V1",
                "prospective_market_date_start": "2026-07-29",
                "predicted_return": 0.001,
                "selection_or_order_use": False,
            }
        ),
        encoding="utf-8",
    )
    result = run(
        str(source),
        replay=True,
        calibrator_path=str(calibrator),
    )
    assert result["fills"][0]["predicted_return"] == 0.001
    assert (
        result["fills"][0]["predicted_return_source"]
        == "PAPER_EXPECTED_RETURN_CALIBRATOR_V1"
    )
