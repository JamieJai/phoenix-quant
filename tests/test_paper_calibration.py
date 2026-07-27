"""Network-free calibration report tests."""
import csv
from pathlib import Path

from scripts.phoenix_paper_calibration import audit


def test_calibration_reports_all_required_comparisons(tmp_path: Path):
    path = tmp_path / "paper.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "predicted_return",
                "forward_return_5m",
                "paper_fill_slippage_bps",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "predicted_return": "0.01",
                "forward_return_5m": "0.008",
                "paper_fill_slippage_bps": "6",
            }
        )
    result = audit(str(path), commission_bps=2.0, estimated_slippage_bps=5.0)
    assert result["status"] == "CALIBRATION_READY"
    assert result["parameter_retuning_allowed"] is False
    assert result["coverage"]["predicted_return"] == 1
    assert result["means"]["realized_net_return"] < result["means"]["realized_gross_return"]


def test_missing_prediction_and_fill_slippage_fail_closed(tmp_path: Path):
    path = tmp_path / "paper.csv"
    path.write_text("forward_return_5m\n0.01\n", encoding="utf-8")
    result = audit(str(path))
    assert result["status"] == "CALIBRATION_INCOMPLETE"
    assert "predicted_return_missing" in result["blocking_reasons"]
    assert "actual_or_paper_fill_slippage_missing" in result["blocking_reasons"]


def test_implausible_realized_return_is_excluded(tmp_path: Path):
    path = tmp_path / "paper.csv"
    path.write_text("forward_return_5m\n12.0\n0.02\n", encoding="utf-8")
    result = audit(str(path))
    assert result["coverage"]["realized_gross_return"] == 1
    assert result["coverage"]["invalid_realized_return_excluded"] == 1
    assert result["means"]["realized_gross_return"] == 0.02
