#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${PHOENIX_ROOT_DIR:-/home/sysadmin/phoenix_ai_core_mvp}"
HOURLY="$ROOT_DIR/scripts/phoenix_hourly_ops.sh"
WEEKLY="$ROOT_DIR/scripts/phoenix_weekly_governance.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

"$HOURLY" --preflight >"$TMP_DIR/hourly_preflight.log"
"$WEEKLY" --preflight >"$TMP_DIR/weekly_preflight.log"

"$HOURLY" --hold-seconds 5 --preflight >"$TMP_DIR/holder.log" 2>&1 &
holder_pid=$!
sleep 1
set +e
"$HOURLY" --preflight >"$TMP_DIR/contender.log" 2>&1
contender_status=$?
"$WEEKLY" --preflight >"$TMP_DIR/cross_job_contender.log" 2>&1
cross_job_status=$?
set -e
wait "$holder_pid"
[[ "$contender_status" -eq 75 ]] || {
  echo "concurrency test failed status=$contender_status"
  exit 1
}
grep -q 'LOCK_BUSY' "$TMP_DIR/contender.log"
[[ "$cross_job_status" -eq 75 ]] || {
  echo "cross-job concurrency test failed status=$cross_job_status" >&2
  exit 1
}
grep -q 'LOCK_BUSY' "$TMP_DIR/cross_job_contender.log"

"$HOURLY" --preflight >"$TMP_DIR/reacquire.log"

set +e
"$HOURLY" --simulate-failure >"$TMP_DIR/failure.log" 2>&1
failure_status=$?
set -e
[[ "$failure_status" -eq 70 ]] || {
  echo "failure simulation returned status=$failure_status"
  exit 1
}
"$HOURLY" --preflight >"$TMP_DIR/recovery.log"

if rg -i 'permission denied' "$TMP_DIR"; then
  echo "permission regression detected"
  exit 1
fi

echo "SCHEDULER_REGRESSION_PASS"
echo "hourly_preflight=PASS weekly_preflight=PASS same_job_concurrency=PASS cross_job_concurrency=PASS lock_release=PASS failure_recovery=PASS permission_denied=0"
