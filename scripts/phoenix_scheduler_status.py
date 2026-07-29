#!/usr/bin/env python3
"""Read-only post-migration scheduler ownership and lock health check."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


LOCK = Path("/home/sysadmin/python-stock/run/locks/scheduler-domain.lock")


def command(*args: str) -> tuple[int, str]:
    result = subprocess.run(args, text=True, capture_output=True)
    return result.returncode, (result.stdout + result.stderr).strip()


def state(*args: str) -> str:
    _, output = command(*args)
    return output.splitlines()[0].strip() if output else "unknown"


def main() -> int:
    root_enabled = state("systemctl", "is-enabled", "phoenix-auto-cycle.timer")
    root_active = state("systemctl", "is-active", "phoenix-auto-cycle.timer")
    root_service = state("systemctl", "is-active", "phoenix-auto-cycle.service")
    hourly_enabled = state(
        "systemctl", "--user", "is-enabled", "python-stock-hourly-ops.timer"
    )
    hourly_active = state(
        "systemctl", "--user", "is-active", "python-stock-hourly-ops.timer"
    )
    weekly_enabled = state(
        "systemctl", "--user", "is-enabled", "python-stock-weekly-governance.timer"
    )
    weekly_active = state(
        "systemctl", "--user", "is-active", "python-stock-weekly-governance.timer"
    )
    pit_enabled = state(
        "systemctl", "--user", "is-enabled", "phoenix-pit-universe-snapshot.timer"
    )
    pit_active = state(
        "systemctl", "--user", "is-active", "phoenix-pit-universe-snapshot.timer"
    )
    toss_enabled = state(
        "systemctl", "--user", "is-enabled", "phoenix-toss-us-research-backfill.timer"
    )
    toss_active = state(
        "systemctl", "--user", "is-active", "phoenix-toss-us-research-backfill.timer"
    )
    sampling_enabled = state(
        "systemctl", "--user", "is-enabled", "phoenix-paper-intraday-sampling.timer"
    )
    sampling_active = state(
        "systemctl", "--user", "is-active", "phoenix-paper-intraday-sampling.timer"
    )
    daily_alert_enabled = state(
        "systemctl", "is-enabled", "phoenix-daily-alert.timer"
    )
    daily_alert_active = state(
        "systemctl", "is-active", "phoenix-daily-alert.timer"
    )
    _, hourly_result_raw = command(
        "systemctl",
        "--user",
        "show",
        "python-stock-hourly-ops.service",
        "-p",
        "Result",
        "--value",
    )
    lock_owner = LOCK.stat().st_uid if LOCK.exists() else None
    _, sysadmin_uid_raw = command("id", "-u", "sysadmin")
    sysadmin_uid = int(sysadmin_uid_raw) if sysadmin_uid_raw.isdigit() else None
    checks = {
        "root_timer_disabled": root_enabled == "disabled",
        "root_timer_inactive": root_active == "inactive",
        "root_service_inactive": root_service == "inactive",
        "hourly_timer_enabled": hourly_enabled == "enabled",
        "hourly_timer_active": hourly_active == "active",
        "weekly_timer_enabled": weekly_enabled == "enabled",
        "weekly_timer_active": weekly_active == "active",
        "pit_timer_enabled": pit_enabled == "enabled",
        "pit_timer_active": pit_active == "active",
        "toss_research_timer_enabled": toss_enabled == "enabled",
        "toss_research_timer_active": toss_active == "active",
        "paper_sampling_timer_enabled": sampling_enabled == "enabled",
        "paper_sampling_timer_active": sampling_active == "active",
        "daily_alert_timer_enabled": daily_alert_enabled == "enabled",
        "daily_alert_timer_active": daily_alert_active == "active",
        "hourly_last_result_success": hourly_result_raw == "success",
        "lock_exists": LOCK.exists(),
        "lock_owned_by_sysadmin": lock_owner == sysadmin_uid,
    }
    healthy = all(checks.values())
    payload = {
        "status": "HEALTHY" if healthy else "DEGRADED",
        "generated_at_utc": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "checks": checks,
        "root": {
            "timer_enabled": root_enabled,
            "timer_active": root_active,
            "service_active": root_service,
        },
        "user": {
            "hourly_enabled": hourly_enabled,
            "hourly_active": hourly_active,
            "hourly_last_result": hourly_result_raw,
            "weekly_enabled": weekly_enabled,
            "weekly_active": weekly_active,
            "pit_enabled": pit_enabled,
            "pit_active": pit_active,
            "toss_research_enabled": toss_enabled,
            "toss_research_active": toss_active,
            "paper_sampling_enabled": sampling_enabled,
            "paper_sampling_active": sampling_active,
        },
        "daily_alert": {
            "enabled": daily_alert_enabled,
            "active": daily_alert_active,
        },
        "lock": {
            "path": str(LOCK),
            "owner": "sysadmin" if lock_owner == sysadmin_uid else str(lock_owner),
            "mechanism": "flock",
        },
        "stale_pre_migration_log_is_current_state": False,
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if healthy else 2


if __name__ == "__main__":
    raise SystemExit(main())
