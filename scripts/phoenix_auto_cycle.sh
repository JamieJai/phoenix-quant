#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${PHOENIX_ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_FILE="${PHOENIX_AUTO_ENV_FILE:-$ROOT_DIR/config/phoenix_auto_cycle.env}"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$ENV_FILE"
fi

PAUSE_FILE="${PHOENIX_PAUSE_FILE:-$ROOT_DIR/.phoenix_auto_cycle.pause}"
if [[ "${PHOENIX_AUTO_CYCLE_DISABLED:-0}" == "1" || -f "$PAUSE_FILE" ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S%z')] auto cycle disabled or pause file exists; exiting"
  exit 0
fi

LOG_DIR="${PHOENIX_LOG_DIR:-$ROOT_DIR/logs}"
LOG_FILE="${PHOENIX_LOG_FILE:-$LOG_DIR/phoenix_auto_validation.log}"
LOCK_FILE="${PHOENIX_LOCK_FILE:-/home/sysadmin/python-stock/run/locks/phoenix-weekly-auto-cycle.lock}"
if [[ ( -e "$LOG_FILE" && ! -w "$LOG_FILE" ) || ( ! -e "$LOG_FILE" && ! -w "$LOG_DIR" ) ]]; then
  LOG_DIR="$ROOT_DIR/reports/runtime_logs"
  LOG_FILE="$LOG_DIR/phoenix_auto_validation.log"
fi
mkdir -p "$LOG_DIR" "$(dirname "$LOCK_FILE")"
exec >>"$LOG_FILE" 2>&1

echo
echo "[$(date '+%Y-%m-%d %H:%M:%S%z')] phoenix auto validation cycle starting"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S%z')] another cycle is already running; exiting"
  exit 0
fi

cd "$ROOT_DIR"

PYTHON_BIN="${PHOENIX_PYTHON:-$(command -v python3)}"
CONFIG_PATH="${PHOENIX_CONFIG:-config/config.yaml}"
TOP_N="${PHOENIX_TOP_N:-5}"
MAIN_PERIOD="${PHOENIX_MAIN_PERIOD:-3y}"
BENCHMARK_PERIOD="${PHOENIX_BENCHMARK_PERIOD:-5y}"
FREQUENCY="${PHOENIX_BENCHMARK_FREQUENCY:-monthly}"
TRAIN_START="${PHOENIX_TRAIN_START:-2023-01-01}"
TRAIN_END="${PHOENIX_TRAIN_END:-2024-12-20}"
TEST_START="${PHOENIX_TEST_START:-2025-01-16}"
TEST_END="${PHOENIX_TEST_END:-$(date -d '3 days ago' '+%Y-%m-%d')}"
RANDOM_BASELINE="${PHOENIX_RANDOM_BASELINE:-1000}"
BOOTSTRAP="${PHOENIX_BOOTSTRAP:-1000}"
TRAIN_TOP_K_RULES="${PHOENIX_TRAIN_TOP_K_RULES:-5}"
RANK_MODE="${PHOENIX_RANK_MODE:-decision}"
XGB_BLEND_WEIGHT="${PHOENIX_XGB_BLEND_WEIGHT:-0.0}"
MIN_DOLLAR_VOLUME="${PHOENIX_MIN_DOLLAR_VOLUME:-10000000}"
MIN_PRICE="${PHOENIX_MIN_PRICE:-5}"
MAX_GAP_OPEN="${PHOENIX_MAX_GAP_OPEN:-0.08}"
ENTRY_PENALTY_BPS="${PHOENIX_ENTRY_PENALTY_BPS:-20}"
EMBARGO_TRADING_DAYS="${PHOENIX_EMBARGO_TRADING_DAYS:-10}"
MODELS_ROOT="${PHOENIX_MODELS_ROOT:-models}"
CANDIDATES_ROOT="${PHOENIX_CANDIDATES_ROOT:-$MODELS_ROOT/candidates}"
CURRENT_DIR="${PHOENIX_CURRENT_DIR:-$MODELS_ROOT/current}"
ARCHIVE_ROOT="${PHOENIX_ARCHIVE_ROOT:-$MODELS_ROOT/archive}"
BOT_SERVICE="${PHOENIX_BOT_SERVICE:-phoenix-telegram-bot.service}"

STAMP="$(date '+%Y%m%d_%H%M%S')"
CANDIDATE_DIR="$CANDIDATES_ROOT/$STAMP"
LEAKAGE_AUDIT_JSON="$CANDIDATE_DIR/leakage_audit.json"
ROLLING_SUMMARY_JSON="$CANDIDATE_DIR/rolling_oos_summary.json"
mkdir -p "$CANDIDATE_DIR"

echo "candidate_dir=$CANDIDATE_DIR"
echo "config=$CONFIG_PATH top_n=$TOP_N train=$TRAIN_START..$TRAIN_END test=$TEST_START..$TEST_END"

MAIN_ARGS=(
  "$PYTHON_BIN" main.py
  --config "$CONFIG_PATH"
  --top
  --top-n "$TOP_N"
  --period "$MAIN_PERIOD"
  --refresh
)
if [[ "${PHOENIX_MAIN_RETRAIN:-0}" == "1" ]]; then
  MAIN_ARGS+=(--retrain)
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S%z')] generating Telegram reference candidates"
PHX_MODELS_DIR="$CANDIDATE_DIR" "${MAIN_ARGS[@]}" >"$CANDIDATE_DIR/top_candidates.txt"

BENCHMARK_ARGS=(
  "$PYTHON_BIN" benchmark.py
  --config "$CONFIG_PATH"
  --train-test
  --train-start "$TRAIN_START"
  --train-end "$TRAIN_END"
  --test-start "$TEST_START"
  --test-end "$TEST_END"
  --top-n "$TOP_N"
  --period "$BENCHMARK_PERIOD"
  --frequency "$FREQUENCY"
  --random-baseline "$RANDOM_BASELINE"
  --bootstrap "$BOOTSTRAP"
  --train-top-k-rules "$TRAIN_TOP_K_RULES"
  --rank-mode "$RANK_MODE"
  --xgb-blend-weight "$XGB_BLEND_WEIGHT"
  --embargo-trading-days "$EMBARGO_TRADING_DAYS"
  --trade-sim
  --min-dollar-volume "$MIN_DOLLAR_VOLUME"
  --min-price "$MIN_PRICE"
  --max-gap-open "$MAX_GAP_OPEN"
  --entry-penalty-bps "$ENTRY_PENALTY_BPS"
)
if [[ -n "${PHOENIX_MAX_DATES:-}" ]]; then
  BENCHMARK_ARGS+=(--max-dates "$PHOENIX_MAX_DATES")
fi
if [[ "${PHOENIX_BENCHMARK_REFRESH:-0}" == "1" ]]; then
  BENCHMARK_ARGS+=(--refresh)
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S%z')] running train/test validation"
PHX_MODELS_DIR="$CANDIDATE_DIR" PHX_REPORTS_DIR="$CANDIDATE_DIR/reports" "${BENCHMARK_ARGS[@]}"

echo "[$(date '+%Y-%m-%d %H:%M:%S%z')] running leakage audit"
set +e
"$PYTHON_BIN" scripts/phoenix_leakage_audit.py   --candidate-dir "$CANDIDATE_DIR"   --write-json "$LEAKAGE_AUDIT_JSON"   --max-test-end-lag-days "${PHOENIX_MAX_TEST_END_LAG_DAYS:-14}"
AUDIT_STATUS=$?
set -e
if [[ "$AUDIT_STATUS" -eq 1 ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S%z')] leakage audit errored; bot restart skipped"
  exit "$AUDIT_STATUS"
elif [[ "$AUDIT_STATUS" -eq 2 ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S%z')] leakage audit failed; gate will reject promotion"
fi

if [[ -n "${PHOENIX_ROLLING_SPLITS:-}" ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S%z')] running rolling OOS validation"
  set +e
  "$PYTHON_BIN" scripts/phoenix_rolling_oos.py     --candidate-dir "$CANDIDATE_DIR"     --splits "$PHOENIX_ROLLING_SPLITS"     --output-json "$ROLLING_SUMMARY_JSON"     --python-bin "$PYTHON_BIN"     --config "$CONFIG_PATH"     --top-n "$TOP_N"     --period "$BENCHMARK_PERIOD"     --frequency "$FREQUENCY"     --random-baseline "$RANDOM_BASELINE"     --bootstrap "$BOOTSTRAP"     --train-top-k-rules "$TRAIN_TOP_K_RULES"     --rank-mode "$RANK_MODE"     --xgb-blend-weight "$XGB_BLEND_WEIGHT"     --embargo-trading-days "$EMBARGO_TRADING_DAYS"     --min-dollar-volume "$MIN_DOLLAR_VOLUME"     --min-price "$MIN_PRICE"     --max-gap-open "$MAX_GAP_OPEN"     --entry-penalty-bps "$ENTRY_PENALTY_BPS"     --min-sample-size "${PHOENIX_MIN_SAMPLE_SIZE:-50}"     --min-active-trades "${PHOENIX_MIN_ACTIVE_TRADES:-30}"     --min-alpha "${PHOENIX_ROLLING_MIN_ALPHA:-0.0}"     --max-p-value "${PHOENIX_ROLLING_MAX_P_VALUE:-0.20}"     --max-mdd "${PHOENIX_ROLLING_MAX_MDD:-0.20}"
  ROLLING_STATUS=$?
  set -e
  if [[ "$ROLLING_STATUS" -eq 1 ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S%z')] rolling OOS errored; bot restart skipped"
    exit "$ROLLING_STATUS"
  elif [[ "$ROLLING_STATUS" -eq 2 ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S%z')] rolling OOS failed; gate will reject promotion if required"
  fi
else
  echo "[$(date '+%Y-%m-%d %H:%M:%S%z')] rolling OOS skipped; PHOENIX_ROLLING_SPLITS is empty"
fi

GATE_ARGS=(
  "$PYTHON_BIN" scripts/phoenix_model_gate.py
  --candidate-dir "$CANDIDATE_DIR"
  --current-dir "$CURRENT_DIR"
  --archive-root "$ARCHIVE_ROOT"
  --min-sample-size "${PHOENIX_MIN_SAMPLE_SIZE:-50}"
  --min-portfolio-delta "${PHOENIX_MIN_PORTFOLIO_DELTA:-0.001}"
  --max-p-value "${PHOENIX_MAX_P_VALUE:-0.20}"
  --max-mdd-slippage "${PHOENIX_MAX_MDD_SLIPPAGE:-0.02}"
  --min-active-trades "${PHOENIX_MIN_ACTIVE_TRADES:-30}"
  --leakage-audit-json "$LEAKAGE_AUDIT_JSON"
  --rolling-summary-json "$ROLLING_SUMMARY_JSON"
  --min-rolling-splits "${PHOENIX_MIN_ROLLING_SPLITS:-2}"
  --min-rolling-pass-rate "${PHOENIX_MIN_ROLLING_PASS_RATE:-1.0}"
)
if [[ "${PHOENIX_ALLOW_INITIAL_PROMOTION:-1}" == "1" ]]; then
  GATE_ARGS+=(--allow-initial-promotion)
fi
if [[ "${PHOENIX_REQUIRE_LEAKAGE_AUDIT:-1}" == "1" ]]; then
  GATE_ARGS+=(--require-leakage-audit)
fi
if [[ "${PHOENIX_REQUIRE_ROLLING_OOS:-0}" == "1" ]]; then
  GATE_ARGS+=(--require-rolling-oos)
fi
if [[ "${PHOENIX_ALLOW_XGB_PROMOTION:-0}" == "1" ]]; then
  GATE_ARGS+=(--allow-xgb-promotion)
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S%z')] evaluating promotion gate"
set +e
"${GATE_ARGS[@]}"
GATE_STATUS=$?
set -e

if [[ "$GATE_STATUS" -eq 0 ]]; then
  if [[ "${PHOENIX_RESTART_BOT_ON_PROMOTION:-1}" == "1" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S%z')] promotion succeeded; restarting $BOT_SERVICE"
    systemctl restart "$BOT_SERVICE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S%z')] $BOT_SERVICE restarted"
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S%z')] research promotion succeeded; live bot restart disabled"
  fi
elif [[ "$GATE_STATUS" -eq 2 ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S%z')] promotion rejected; bot restart skipped"
else
  echo "[$(date '+%Y-%m-%d %H:%M:%S%z')] promotion gate errored; bot restart skipped"
  exit "$GATE_STATUS"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S%z')] phoenix auto validation cycle finished"
