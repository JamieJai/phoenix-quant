"""Fail-closed durable review interlock with no broker implementation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any


def _timestamp(value: object) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if result.tzinfo is None:
        return None
    return result.astimezone(timezone.utc)


def validate_review_approval(
    path: str | os.PathLike[str],
    *,
    expected_commit: str,
    now: datetime | None = None,
    maximum_validity_hours: int = 24,
) -> tuple[bool, list[str], dict[str, Any]]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    approval_path = Path(path)
    reasons: list[str] = []
    if not approval_path.exists():
        return False, ["APPROVAL_MISSING"], {}
    try:
        mode = approval_path.stat().st_mode & 0o777
        if mode & 0o077:
            reasons.append("APPROVAL_PERMISSIONS_UNSAFE")
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, ["APPROVAL_UNREADABLE"], {}
    created = _timestamp(approval.get("created_at_utc"))
    expires = _timestamp(approval.get("expires_at_utc"))
    if approval.get("status") != "APPROVED":
        reasons.append("APPROVAL_STATUS_INVALID")
    if approval.get("scope") != "LIVE_REVIEW_ONLY":
        reasons.append("APPROVAL_SCOPE_INVALID")
    if not str(approval.get("operator", "")).strip():
        reasons.append("APPROVAL_OPERATOR_MISSING")
    if approval.get("commit_sha") != expected_commit:
        reasons.append("APPROVAL_COMMIT_MISMATCH")
    if approval.get("broker_orders_authorized") is not False:
        reasons.append("BROKER_AUTHORIZATION_FORBIDDEN")
    if created is None or expires is None:
        reasons.append("APPROVAL_TIMESTAMP_INVALID")
    else:
        if created > now:
            reasons.append("APPROVAL_FROM_FUTURE")
        if expires <= now:
            reasons.append("APPROVAL_EXPIRED")
        if expires - created > timedelta(hours=maximum_validity_hours):
            reasons.append("APPROVAL_VALIDITY_TOO_LONG")
    return not reasons, sorted(set(reasons)), approval


def evaluate_review_interlock(
    *,
    approval_path: str | os.PathLike[str],
    kill_switch_path: str | os.PathLike[str],
    readiness: dict[str, Any],
    expected_commit: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    reasons: list[str] = []
    kill_path = Path(kill_switch_path)
    try:
        os.lstat(kill_path)
    except FileNotFoundError:
        pass
    except OSError:
        reasons.append("KILL_SWITCH_STATE_UNREADABLE")
    else:
        reasons.append("KILL_SWITCH_ARMED")
    approval_valid, approval_reasons, approval = validate_review_approval(
        approval_path,
        expected_commit=expected_commit,
        now=now,
    )
    if not approval_valid:
        reasons.extend(approval_reasons)
    if readiness.get("status") != "LIVE_REVIEW_READY":
        reasons.append("READINESS_NOT_READY")
    if readiness.get("live_enabled") is not False:
        reasons.append("LIVE_STATE_UNSAFE")
    if readiness.get("broker_enabled") is not False:
        reasons.append("BROKER_STATE_UNSAFE")
    allowed = not reasons
    return {
        "status": "REVIEW_ALLOWED" if allowed else "BLOCKED",
        "reasons": sorted(set(reasons)),
        "approval_operator": approval.get("operator") if approval else None,
        "approval_commit": approval.get("commit_sha") if approval else None,
        "review_allowed": allowed,
        "broker_route_available": False,
        "broker_orders_authorized": False,
        "account_endpoints_called": False,
        "order_endpoints_called": False,
        "live_enabled": False,
    }
