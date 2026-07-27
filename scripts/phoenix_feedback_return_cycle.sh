#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${PHOENIX_ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_FILE="${PHOENIX_FEEDBACK_ENV_FILE:-$ROOT_DIR/config/phoenix_auto_cycle.env}"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$ENV_FILE"
fi

cd "$ROOT_DIR"

PYTHON_BIN="${PHOENIX_PYTHON:-$(command -v python3)}"
CONFIG_PATH="${PHOENIX_CONFIG:-config/config.yaml}"
CACHE_DIR="${PHOENIX_FEEDBACK_CACHE_DIR:-data}"
FEEDBACK_CSV="${PHOENIX_FEEDBACK_CSV:-data/operator_feedback.csv}"
MANIFEST_PATH="${PHOENIX_DAILY_DATA_MANIFEST:-$CACHE_DIR/daily_data_manifest.csv}"
PERIOD="${PHOENIX_DAILY_DATA_PERIOD:-5y}"
MAX_AGE_DAYS="${PHOENIX_DAILY_MAX_AGE_DAYS:-7}"
LOG_DIR="${PHOENIX_LOG_DIR:-$ROOT_DIR/logs}"
LOG_FILE="${PHOENIX_FEEDBACK_LOG_FILE:-$LOG_DIR/phoenix_feedback_return_cycle.log}"
LOCK_FILE="${PHOENIX_FEEDBACK_LOCK_FILE:-/home/sysadmin/python-stock/run/locks/phoenix-feedback-return-cycle.lock}"
DISABLED="${PHOENIX_FEEDBACK_RETURN_DISABLED:-0}"

if [[ ( -e "$LOG_FILE" && ! -w "$LOG_FILE" ) || ( ! -e "$LOG_FILE" && ! -w "$LOG_DIR" ) ]]; then
  LOG_DIR="$ROOT_DIR/reports/runtime_logs"
  LOG_FILE="$LOG_DIR/phoenix_feedback_return_cycle.log"
fi
mkdir -p "$LOG_DIR" "$(dirname "$LOCK_FILE")"
exec >>"$LOG_FILE" 2>&1

echo
echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] phoenix feedback return cycle starting"

if [[ "$DISABLED" == "1" ]]; then
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] feedback return cycle disabled; exiting"
  exit 0
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] another feedback return cycle is already running; exiting"
  exit 0
fi

echo "config=$CONFIG_PATH cache_dir=$CACHE_DIR feedback_csv=$FEEDBACK_CSV period=$PERIOD"

set +e
"$PYTHON_BIN" scripts/fetch_daily_data.py \
  --config "$CONFIG_PATH" \
  --period "$PERIOD" \
  --cache-dir "$CACHE_DIR" \
  --refresh \
  --max-age-days "$MAX_AGE_DAYS" \
  --manifest "$MANIFEST_PATH"
FETCH_STATUS=$?
set -e
if [[ "$FETCH_STATUS" -ne 0 ]]; then
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] daily data refresh returned status=$FETCH_STATUS; attempting feedback update anyway"
fi

"$PYTHON_BIN" scripts/phoenix_update_feedback_returns.py \
  --feedback-csv "$FEEDBACK_CSV" \
  --cache-dir "$CACHE_DIR"

"$PYTHON_BIN" scripts/phoenix_feedback_summary.py \
  --feedback-csv "$FEEDBACK_CSV"

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] phoenix feedback return cycle finished"
