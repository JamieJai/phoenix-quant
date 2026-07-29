#!/usr/bin/env python3
"""Validate the durable review interlock without creating live authority."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from phoenix_core.trade.execution_interlock import (  # noqa: E402
    evaluate_review_interlock,
)

PREREG = ROOT / (
    "research/preregistrations/DURABLE_EXECUTION_INTERLOCK_V1.json"
)
IMPLEMENTATION = ROOT / "phoenix_core/trade/execution_interlock.py"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _approval(
    path: Path,
    *,
    commit: str,
    created: datetime,
    expires: datetime,
) -> None:
    path.write_text(
        json.dumps(
            {
                "status": "APPROVED",
                "scope": "LIVE_REVIEW_ONLY",
                "operator": "validation-only",
                "commit_sha": commit,
                "created_at_utc": created.isoformat(),
                "expires_at_utc": expires.isoformat(),
                "broker_orders_authorized": False,
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def validate() -> dict[str, object]:
    now = datetime(2026, 7, 29, 14, 0, tzinfo=timezone.utc)
    commit = "validation-commit"
    readiness = {
        "status": "LIVE_REVIEW_READY",
        "live_enabled": False,
        "broker_enabled": False,
    }
    with TemporaryDirectory() as directory:
        root = Path(directory)
        approval = root / "approval.json"
        kill_switch = root / "kill-switch"
        missing = evaluate_review_interlock(
            approval_path=approval,
            kill_switch_path=kill_switch,
            readiness=readiness,
            expected_commit=commit,
            now=now,
        )
        _approval(
            approval,
            commit=commit,
            created=now - timedelta(minutes=1),
            expires=now + timedelta(hours=1),
        )
        kill_switch.write_text("armed\n", encoding="utf-8")
        armed = evaluate_review_interlock(
            approval_path=approval,
            kill_switch_path=kill_switch,
            readiness=readiness,
            expected_commit=commit,
            now=now,
        )
        kill_switch.unlink()
        expired_path = root / "expired.json"
        _approval(
            expired_path,
            commit=commit,
            created=now - timedelta(hours=2),
            expires=now - timedelta(hours=1),
        )
        expired = evaluate_review_interlock(
            approval_path=expired_path,
            kill_switch_path=kill_switch,
            readiness=readiness,
            expected_commit=commit,
            now=now,
        )
        mismatch = evaluate_review_interlock(
            approval_path=approval,
            kill_switch_path=kill_switch,
            readiness=readiness,
            expected_commit="different-commit",
            now=now,
        )
        valid = evaluate_review_interlock(
            approval_path=approval,
            kill_switch_path=kill_switch,
            readiness=readiness,
            expected_commit=commit,
            now=now,
        )
    checks = {
        "missing_approval_blocks": (
            missing["status"] == "BLOCKED"
            and "APPROVAL_MISSING" in missing["reasons"]
        ),
        "armed_kill_switch_blocks": (
            armed["status"] == "BLOCKED"
            and "KILL_SWITCH_ARMED" in armed["reasons"]
        ),
        "expired_approval_blocks": (
            expired["status"] == "BLOCKED"
            and "APPROVAL_EXPIRED" in expired["reasons"]
        ),
        "commit_mismatch_blocks": (
            mismatch["status"] == "BLOCKED"
            and "APPROVAL_COMMIT_MISMATCH" in mismatch["reasons"]
        ),
        "valid_review_approval_allows_review_only": (
            valid["status"] == "REVIEW_ALLOWED"
            and valid["review_allowed"] is True
            and valid["broker_orders_authorized"] is False
            and valid["broker_route_available"] is False
        ),
        "no_account_or_order_endpoint": (
            valid["account_endpoints_called"] is False
            and valid["order_endpoints_called"] is False
            and valid["live_enabled"] is False
        ),
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "evidence_type": "DURABLE_EXECUTION_INTERLOCK_VALIDATION_V1",
        "validated_at_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z"),
        "checks": checks,
        "preregistration": {
            "artifact": str(PREREG.relative_to(ROOT)),
            "sha256": sha256(PREREG),
        },
        "implementation": {
            "artifact": str(IMPLEMENTATION.relative_to(ROOT)),
            "sha256": sha256(IMPLEMENTATION),
        },
        "approval_artifact_created": False,
        "kill_switch_disarmed": False,
        "broker_routes_called": False,
        "account_endpoints_called": False,
        "order_endpoints_called": False,
        "live_enabled": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=(
            "reports/paper_trading/interlock/"
            "execution_interlock_validation_latest.json"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = validate()
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
        else result["status"]
    )
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
