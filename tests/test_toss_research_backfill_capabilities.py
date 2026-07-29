"""Network-free tests for terminal source-capability handling."""
import json
from pathlib import Path

from scripts.phoenix_toss_us_extended_hours_backfill import (
    DATASET,
    SOURCE,
    load_source_capabilities,
    summarize,
    write_terminal_unavailable_manifest,
)


def _contract(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "source": SOURCE,
                "dataset": DATASET,
                "terminal_unavailable_symbols": [
                    {
                        "ticker": "^VIX",
                        "reason_code": "TOSS_CANDLE_SYMBOL_UNSUPPORTED",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_terminal_unavailable_is_finalized_without_imputation(tmp_path: Path):
    capability_path = tmp_path / "capabilities.json"
    _contract(capability_path)
    _, unavailable = load_source_capabilities(capability_path)

    root = tmp_path / "dataset"
    manifest_path = write_terminal_unavailable_manifest(
        root,
        ticker="^VIX",
        market_date="2026-07-28",
        capability_path=capability_path,
        capability_sha256="test-sha",
        capability=unavailable["^VIX"],
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["collection_status"] == "SOURCE_UNSUPPORTED"
    assert manifest["row_count"] == 0
    assert manifest["raw"] is None
    assert manifest["normalized"] is None
    assert manifest["missing_value_policy"]["bars_imputed"] is False
    assert manifest["missing_value_policy"]["eligible_for_signal"] is False


def test_summary_separates_data_and_terminal_missing(tmp_path: Path):
    capability_path = tmp_path / "capabilities.json"
    _contract(capability_path)
    root = tmp_path / "dataset"
    _, unavailable = load_source_capabilities(capability_path)
    write_terminal_unavailable_manifest(
        root,
        ticker="^VIX",
        market_date="2026-07-28",
        capability_path=capability_path,
        capability_sha256="test-sha",
        capability=unavailable["^VIX"],
    )
    plan = {
        "flags": ["HISTORICAL_EXPLORATORY_ONLY"],
        "market_dates": ["2026-07-28"],
        "minimum_sessions": 1,
        "plan_sha256": "plan-sha",
        "target_sessions": 1,
        "ticker_count": 1,
        "total_ticker_dates": 1,
    }

    status = summarize(
        root,
        plan,
        requests=0,
        retries=0,
        capability_path=capability_path,
        capability_sha256="test-sha",
    )

    assert status["status"] == "READY_EXPLORATORY"
    assert status["complete_sessions"] == 1
    assert status["complete_data_sessions"] == 0
    assert status["data_artifact_ticker_dates"] == 0
    assert status["terminal_missing_ticker_dates"] == 1
    assert status["premarket_exploratory_allowed"] is True
