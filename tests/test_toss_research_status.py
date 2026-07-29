"""Network-free tests for Toss research pipeline health reporting."""
import json
from pathlib import Path

from scripts.phoenix_toss_research_status import audit


def test_status_fails_closed_when_artifacts_are_missing(tmp_path: Path):
    result = audit("missing", "cap.json", repository_root=tmp_path)
    assert result["status"] == "DEGRADED"
    assert result["production_connected"] is False
    assert result["champion_connected"] is False


def test_status_detects_stalled_error_batch(tmp_path: Path):
    root = tmp_path / "dataset"
    root.mkdir()
    capability = tmp_path / "cap.json"
    capability.write_text(
        json.dumps(
            {
                "terminal_unavailable_symbols": [],
                "source": "TOSS_US",
                "dataset": "TOSS_US_EXTENDED_HOURS_DATASET_V1_RESEARCH",
            }
        ),
        encoding="utf-8",
    )
    plan = {
        "plan_sha256": "plan",
        "session_count": 1,
    }
    status = {
        "plan_sha256": "plan",
        "source_capability_contract": {"sha256": ""},
        "production_connected": False,
        "champion_connected": False,
        "terminal_missing_ticker_dates": 0,
        "updated_at_utc": "2026-07-29T01:00:00Z",
        "status": "COLLECTING",
    }
    last_run = {"errors_this_run": [{"error": "x"}], "processed_this_run": 0}
    (root / "dataset_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (root / "dataset_status.json").write_text(json.dumps(status), encoding="utf-8")
    (root / "last_run.json").write_text(json.dumps(last_run), encoding="utf-8")

    status["source_capability_contract"]["sha256"] = __import__(
        "scripts.phoenix_toss_research_status", fromlist=["sha256"]
    ).sha256(capability)
    (root / "dataset_status.json").write_text(json.dumps(status), encoding="utf-8")
    result = audit(
        "dataset",
        "cap.json",
        max_status_age_minutes=1_000_000,
        repository_root=tmp_path,
    )
    assert result["status"] == "DEGRADED"
    assert "last_batch_stalled_on_errors" in result["failure_reasons"]
