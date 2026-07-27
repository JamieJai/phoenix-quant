#!/usr/bin/env bash
set -Eeuo pipefail

PHOENIX_DIR="${PHOENIX_ROOT_DIR:-/home/sysadmin/phoenix_ai_core_mvp}"
PYTHON_STOCK_DIR="${PYTHON_STOCK_DIR:-/home/sysadmin/python-stock}"
RUNTIME_DIR="${PYTHON_STOCK_RUNTIME_DIR:-$PYTHON_STOCK_DIR/run}"
LOCK_DIR="${PYTHON_STOCK_LOCK_DIR:-$RUNTIME_DIR/locks}"
LOCK_FILE="${WEEKLY_GOVERNANCE_LOCK_FILE:-$LOCK_DIR/scheduler-domain.lock}"
STATUS_DIR="${WEEKLY_GOVERNANCE_STATUS_DIR:-$RUNTIME_DIR/status/weekly}"
AUTO_TRAIN_ENV="${AUTO_TRAIN_ENV_FILE:-$PYTHON_STOCK_DIR/config/auto_train.env}"
PHOENIX_ENV="${PHOENIX_AUTO_ENV_FILE:-$PYTHON_STOCK_DIR/config/phoenix_auto_cycle.runtime.env}"
PREFLIGHT=0
SIMULATE_FAILURE=0
HOLD_SECONDS=0
RUN_USER="$(id -un)"

while (($#)); do
  case "$1" in
    --preflight|--dry-run) PREFLIGHT=1 ;;
    --simulate-failure) SIMULATE_FAILURE=1 ;;
    --hold-seconds)
      shift
      HOLD_SECONDS="${1:?--hold-seconds requires a value}"
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 64
      ;;
  esac
  shift
done

if [[ "$RUN_USER" != "sysadmin" ]]; then
  echo "OWNERSHIP_ERROR expected=sysadmin actual=$RUN_USER" >&2
  exit 77
fi

mkdir -p "$LOCK_DIR" "$STATUS_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "LOCK_BUSY path=$LOCK_FILE"
  exit 75
fi

required=(
  "$PYTHON_STOCK_DIR/scripts/auto_train_cycle.sh"
  "$PHOENIX_DIR/scripts/phoenix_auto_cycle.sh"
  "$AUTO_TRAIN_ENV"
  "$PHOENIX_ENV"
)
for path in "${required[@]}"; do
  [[ -e "$path" ]] || {
    echo "PRECHECK_FAILED missing=$path"
    exit 66
  }
done

grep -q '^AUTO_TRAIN_DRY_RUN=true$' "$AUTO_TRAIN_ENV" || {
  echo "PRECHECK_FAILED python-stock promotion is not dry-run"
  exit 65
}
grep -q '^PHOENIX_CURRENT_DIR=models/research_current$' "$PHOENIX_ENV" || {
  echo "PRECHECK_FAILED Phoenix current dir is not research-only"
  exit 65
}
grep -q '^PHOENIX_RESTART_BOT_ON_PROMOTION=0$' "$PHOENIX_ENV" || {
  echo "PRECHECK_FAILED Phoenix bot restart is not disabled"
  exit 65
}

if ((HOLD_SECONDS > 0)); then
  sleep "$HOLD_SECONDS"
fi
if ((SIMULATE_FAILURE)); then
  echo "SIMULATED_FAILURE"
  exit 70
fi
if ((PREFLIGHT)); then
  echo "PREFLIGHT_OK lock=$LOCK_FILE owner=sysadmin"
  echo "PLAN python-stock=dry-run Phoenix=models/research_current"
  exit 0
fi

RUN_ID="$(date -u '+%Y%m%dT%H%M%SZ')"
RUN_DIR="$STATUS_DIR/$RUN_ID"
mkdir -p "$RUN_DIR"

AUTO_TRAIN_ENV_FILE="$AUTO_TRAIN_ENV" \
  "$PYTHON_STOCK_DIR/scripts/auto_train_cycle.sh"

PHOENIX_AUTO_ENV_FILE="$PHOENIX_ENV" \
  "$PHOENIX_DIR/scripts/phoenix_auto_cycle.sh"

printf '{"run_id":"%s","finished_at_utc":"%s","status":"WEEKLY_COMPLETE","champion_changed":false}\n' \
  "$RUN_ID" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >"$RUN_DIR/status.json"
echo "WEEKLY_OK run_dir=$RUN_DIR"
