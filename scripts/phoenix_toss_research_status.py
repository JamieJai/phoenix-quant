#!/usr/bin/env python3
"""Fail-closed filesystem audit for the Toss US research dataset pipeline."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = (
    "data/research/toss_us/"
    "extended_hours_dataset_v1_research/governed_v1"
)
DEFAULT_CAPABILITIES = (
    "research/source_capabilities/"
    "TOSS_US_EXTENDED_HOURS_DATASET_V1_RESEARCH.json"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def audit(
    dataset_root: str = DEFAULT_ROOT,
    capability_path: str = DEFAULT_CAPABILITIES,
    *,
    max_status_age_minutes: float = 60.0,
    repository_root: Path | None = None,
) -> dict[str, object]:
    base = repository_root or ROOT
    root = base / dataset_root
    paths = {
        "plan": root / "dataset_plan.json",
        "status": root / "dataset_status.json",
        "last_run": root / "last_run.json",
        "capability": base / capability_path,
    }
    missing = [name for name, path in paths.items() if not path.exists()]
    if missing:
        return {
            "status": "DEGRADED",
            "operational_health": "DEGRADED",
            "failure_reasons": [f"missing_artifact:{name}" for name in missing],
            "production_connected": False,
            "champion_connected": False,
        }

    plan = _read(paths["plan"])
    status = _read(paths["status"])
    last_run = _read(paths["last_run"])
    capabilities = _read(paths["capability"])
    failure_reasons: list[str] = []

    if status.get("plan_sha256") != plan.get("plan_sha256"):
        failure_reasons.append("plan_sha_mismatch")
    capability_sha = sha256(paths["capability"])
    recorded_capability_sha = (
        status.get("source_capability_contract", {}).get("sha256")
    )
    if recorded_capability_sha != capability_sha:
        failure_reasons.append("source_capability_sha_mismatch")
    if status.get("production_connected") is not False:
        failure_reasons.append("production_connection_not_false")
    if status.get("champion_connected") is not False:
        failure_reasons.append("champion_connection_not_false")
    if last_run.get("errors_this_run") and not last_run.get("processed_this_run"):
        failure_reasons.append("last_batch_stalled_on_errors")

    unavailable_count = len(capabilities.get("terminal_unavailable_symbols", []))
    expected_terminal = unavailable_count * int(plan.get("session_count", 0))
    observed_terminal = int(status.get("terminal_missing_ticker_dates", 0) or 0)
    if observed_terminal != expected_terminal:
        failure_reasons.append(
            f"terminal_manifest_count:{observed_terminal}!={expected_terminal}"
        )

    updated = status.get("updated_at_utc")
    age_minutes = None
    if updated:
        try:
            timestamp = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
            age_minutes = (
                datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)
            ).total_seconds() / 60.0
        except ValueError:
            failure_reasons.append("invalid_status_timestamp")
    else:
        failure_reasons.append("missing_status_timestamp")
    if age_minutes is not None and age_minutes > max_status_age_minutes:
        failure_reasons.append(
            f"status_stale_minutes:{age_minutes:.1f}>{max_status_age_minutes:.1f}"
        )

    dataset_state = status.get("status", "UNKNOWN")
    operational_health = "DEGRADED" if failure_reasons else "HEALTHY"
    research_status = (
        "DEGRADED"
        if failure_reasons
        else ("READY" if dataset_state == "READY_EXPLORATORY" else "EXPERIMENTAL")
    )
    return {
        "status": research_status,
        "operational_health": operational_health,
        "dataset_state": dataset_state,
        "completed_ticker_dates": status.get("completed_ticker_dates"),
        "data_artifact_ticker_dates": status.get("data_artifact_ticker_dates"),
        "terminal_missing_ticker_dates": observed_terminal,
        "completion_ratio": status.get("completion_ratio"),
        "complete_sessions": status.get("complete_sessions"),
        "minimum_sessions": status.get("minimum_sessions"),
        "last_processed": last_run.get("processed_this_run"),
        "last_errors": len(last_run.get("errors_this_run", [])),
        "status_age_minutes": round(age_minutes, 2) if age_minutes is not None else None,
        "plan_sha256": plan.get("plan_sha256"),
        "source_capability_sha256": capability_sha,
        "failure_reasons": failure_reasons,
        "premarket_exploratory_allowed": status.get(
            "premarket_exploratory_allowed", False
        ),
        "confirmatory_use_allowed": False,
        "production_connected": False,
        "champion_connected": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", default=DEFAULT_ROOT)
    parser.add_argument("--source-capabilities", default=DEFAULT_CAPABILITIES)
    parser.add_argument("--max-status-age-minutes", type=float, default=60.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = audit(
        args.dataset_root,
        args.source_capabilities,
        max_status_age_minutes=args.max_status_age_minutes,
    )
    print(
        json.dumps(result, ensure_ascii=False)
        if args.json
        else f"{result['status']} {result.get('failure_reasons', [])}"
    )
    return 2 if result["status"] == "DEGRADED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
