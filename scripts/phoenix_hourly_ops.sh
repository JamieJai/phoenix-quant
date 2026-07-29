#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${PHOENIX_ROOT_DIR:-/home/sysadmin/phoenix_ai_core_mvp}"
PYTHON_STOCK_DIR="${PYTHON_STOCK_DIR:-/home/sysadmin/python-stock}"
RUNTIME_DIR="${PYTHON_STOCK_RUNTIME_DIR:-$PYTHON_STOCK_DIR/run}"
LOCK_DIR="${PYTHON_STOCK_LOCK_DIR:-$RUNTIME_DIR/locks}"
LOCK_FILE="${PHOENIX_HOURLY_LOCK_FILE:-$LOCK_DIR/scheduler-domain.lock}"
STATUS_DIR="${PHOENIX_HOURLY_STATUS_DIR:-$RUNTIME_DIR/status/hourly}"
LOG_DIR="${PHOENIX_HOURLY_LOG_DIR:-$PYTHON_STOCK_DIR/logs/scheduler}"
PYTHON_BIN="${PHOENIX_PYTHON:-$ROOT_DIR/.venv/bin/python}"
PRE_FLIGHT=0
SIMULATE_FAILURE=0
HOLD_SECONDS=0
RUN_USER="$(id -un)"

while (($#)); do
  case "$1" in
    --preflight) PRE_FLIGHT=1 ;;
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

mkdir -p "$LOCK_DIR" "$STATUS_DIR" "$LOG_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "LOCK_BUSY path=$LOCK_FILE"
  exit 75
fi

STARTED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
RUN_ID="$(date -u '+%Y%m%dT%H%M%SZ')"
RUN_DIR="$STATUS_DIR/$RUN_ID"
mkdir -p "$RUN_DIR"

finish() {
  local status=$?
  printf '{"run_id":"%s","started_at_utc":"%s","finished_at_utc":"%s","status":%d,"lock_path":"%s","lock_owner":"sysadmin"}\n' \
    "$RUN_ID" "$STARTED_AT" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$status" "$LOCK_FILE" \
    >"$RUN_DIR/status.json"
  exit "$status"
}
trap finish EXIT

required=(
  "$PYTHON_BIN"
  "$ROOT_DIR/scripts/fetch_daily_data.py"
  "$ROOT_DIR/scripts/phoenix_data_coverage_audit.py"
  "$ROOT_DIR/scripts/phoenix_update_feedback_returns.py"
  "$ROOT_DIR/scripts/phoenix_feedback_summary.py"
  "$ROOT_DIR/scripts/phoenix_intraday_label_cache.py"
  "$ROOT_DIR/scripts/phoenix_intraday_cache_schema.py"
  "$ROOT_DIR/scripts/phoenix_paper_signal_runner.py"
  "$ROOT_DIR/reports/paper_trading/calibration/paper_expected_return_calibrator_v1.json"
  "$ROOT_DIR/scripts/phoenix_paper_pnl_report.py"
  "$ROOT_DIR/scripts/phoenix_paper_calibration.py"
  "$ROOT_DIR/scripts/phoenix_paper_regime_evidence.py"
  "$ROOT_DIR/scripts/phoenix_paper_base_oos_evidence.py"
  "$ROOT_DIR/scripts/phoenix_portfolio_risk_validation.py"
  "$ROOT_DIR/scripts/phoenix_shadow_ledger_validation.py"
  "$ROOT_DIR/scripts/phoenix_shadow_portfolio_status.py"
  "$ROOT_DIR/scripts/phoenix_kill_switch_validation.py"
  "$ROOT_DIR/scripts/phoenix_toss_research_status.py"
  "$ROOT_DIR/scripts/phoenix_live_readiness.py"
  "$ROOT_DIR/scripts/phoenix_auto_status.py"
)
for path in "${required[@]}"; do
  [[ -e "$path" ]] || {
    echo "PRECHECK_FAILED missing=$path"
    exit 66
  }
done

if ((HOLD_SECONDS > 0)); then
  sleep "$HOLD_SECONDS"
fi
if ((SIMULATE_FAILURE)); then
  echo "SIMULATED_FAILURE"
  exit 70
fi
if ((PRE_FLIGHT)); then
  echo "PREFLIGHT_OK lock=$LOCK_FILE owner=sysadmin"
  exit 0
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" scripts/fetch_daily_data.py \
  --config config/config.yaml \
  --period 5y \
  --cache-dir data \
  --refresh \
  --max-age-days 4 \
  --manifest data/daily_data_manifest.csv \
  >"$RUN_DIR/data_refresh.txt" 2>&1

"$PYTHON_BIN" scripts/phoenix_data_coverage_audit.py \
  --config config/config.yaml \
  --cache-dir data \
  --include-etfs \
  --max-age-days 4 \
  --min-split-coverage 0.90 \
  --min-universe-usable-ratio 0.90 \
  --json >"$RUN_DIR/data_coverage.json"

"$PYTHON_BIN" scripts/phoenix_update_feedback_returns.py \
  --feedback-csv data/operator_feedback.csv \
  --cache-dir data >"$RUN_DIR/feedback_update.txt"
"$PYTHON_BIN" scripts/phoenix_feedback_summary.py \
  --feedback-csv data/operator_feedback.csv >"$RUN_DIR/feedback_summary.txt"
"$PYTHON_BIN" scripts/phoenix_intraday_cache_schema.py \
  --path data/intraday_features.csv \
  --json >"$RUN_DIR/intraday_cache_schema.json"
"$PYTHON_BIN" scripts/phoenix_intraday_label_cache.py \
  --path data/intraday_features.csv \
  --fetch-matured \
  --prospective-start 2026-07-29 >"$RUN_DIR/intraday_labels.txt"
"$PYTHON_BIN" scripts/phoenix_paper_signal_runner.py \
  --path data/intraday_features.csv \
  --limit 500 \
  --replay \
  --calibrator reports/paper_trading/calibration/paper_expected_return_calibrator_v1.json \
  --output-json "$RUN_DIR/paper_signals.json" \
  --fills-csv "$RUN_DIR/paper_fills.csv" \
  >"$RUN_DIR/paper_replay.txt"
"$PYTHON_BIN" scripts/phoenix_paper_pnl_report.py \
  --path data/intraday_features.csv \
  --json >"$RUN_DIR/paper_pnl.json"
"$PYTHON_BIN" scripts/phoenix_paper_calibration.py \
  --path "$RUN_DIR/paper_fills.csv" \
  --output-json reports/paper_trading/calibration/paper_calibration_latest.json \
  --json >"$RUN_DIR/paper_calibration.json"
"$PYTHON_BIN" scripts/phoenix_paper_regime_evidence.py \
  --json >"$RUN_DIR/paper_regime_evidence.json"
"$PYTHON_BIN" scripts/phoenix_paper_base_oos_evidence.py \
  --json >"$RUN_DIR/paper_base_oos_evidence.json"
"$PYTHON_BIN" scripts/phoenix_portfolio_risk_validation.py \
  --json >"$RUN_DIR/portfolio_risk_validation.json"
"$PYTHON_BIN" scripts/phoenix_shadow_ledger_validation.py \
  --json >"$RUN_DIR/shadow_ledger_validation.json"
"$PYTHON_BIN" scripts/phoenix_shadow_portfolio_status.py \
  --json >"$RUN_DIR/shadow_portfolio_status.json"
"$PYTHON_BIN" scripts/phoenix_kill_switch_validation.py \
  --json >"$RUN_DIR/kill_switch_validation.json"
"$PYTHON_BIN" scripts/phoenix_toss_research_status.py \
  --json >"$RUN_DIR/toss_research_status.json"
"$PYTHON_BIN" scripts/phoenix_live_readiness.py \
  --cache data/intraday_features.csv \
  --config config/paper_trading.yaml \
  --json >"$RUN_DIR/live_readiness.json"
"$PYTHON_BIN" scripts/phoenix_auto_status.py \
  --models-root models \
  --log-file "$LOG_DIR/hourly-operations.log" \
  --json >"$RUN_DIR/model_health.json"

echo "HOURLY_OK run_dir=$RUN_DIR"
