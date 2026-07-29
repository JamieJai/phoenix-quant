# Phoenix Auto Learning Operations

This project uses champion/challenger promotion for Telegram reference candidate models and rules. It does not add automatic trading.

## Daily Check

Run:

```bash
cd /home/sysadmin/phoenix_ai_core_mvp
./scripts/phoenix_auto_status.py
```

Review:

- `Current model` for the active promoted model/rule.
- Recent candidate `status` values.
- `gate.reasons` for rejected candidates.
- `leakage_passed` must be true for promotable candidates.
- `rolling_passed` must be true when rolling OOS is required.
- Log tail for cycle errors.


## Failure Analysis

After at least one auto-cycle candidate exists, run:

```bash
./scripts/phoenix_failure_analysis.py --limit 50
```

Use the output to pick the next engineering task. Prioritize repeated failure categories instead of isolated failures.

Common categories:

- `p_value`: candidate is not statistically better than random baseline. Do not loosen this first.
- `rolling_oos`: performance is not stable across windows. Inspect regime-specific weakness.
- `leakage_audit`: validation setup is unsafe. Fix this before trusting metrics.
- `xgb_or_rank_mode`: XGB/ranking promotion is blocked by policy. Keep blocked until separately proven.
- `mdd`: risk profile is too weak. Inspect gap, liquidity, sector stress, and trade rule settings.
- `alpha`: candidate is not beating baseline enough. Study feature quality before adding complexity.
- `active_trades` or `sample_size`: evidence is too sparse. Do not promote low-sample candidates.


## Pausing Scheduled Auto-Cycle

Before long manual OOS experiments, pause scheduled auto-cycle jobs so they do not compete for CPU or create low-confidence candidates from known-bad windows. Either set this in the host-local env:

```bash
PHOENIX_AUTO_CYCLE_DISABLED=1
```

or create the pause file configured by `PHOENIX_PAUSE_FILE`:

```bash
touch .phoenix_auto_cycle.pause
```

Remove the file or set the flag back to `0` to resume future scheduled cycles. Already-running root-owned jobs may still need to finish or be stopped by an operator with sufficient privileges.

## Data Coverage Audit

Before interpreting weak OOS results, check whether the cached OHLCV data actually covers each train/test window:

```bash
.venv/bin/python scripts/phoenix_data_coverage_audit.py --include-etfs \
  --split split_2024:2022-01-03,2023-12-15,2024-01-08,2024-12-20 \
  --split split_2025_2026:2023-01-01,2024-12-20,2025-01-16,2026-07-06
```

The audit writes `ticker_coverage.csv`, `split_coverage.csv`, and `summary.json` under `reports/data_coverage/`. If train coverage is weak, refresh data first and then re-run the audit:

```bash
.venv/bin/python scripts/fetch_daily_data.py --period 5y --refresh --manifest data/daily_data_manifest.csv
```

Do not trust a rolling split whose train phase has too few usable tickers; it may be measuring data availability rather than model quality.


## Adverse-Risk Post-Filter Experiment

Use this only as an explicit OOS experiment; leave it empty for baseline behavior. The 2026-07-12 diagnostics found `NLR,XLK` sector skips materially improved the adjusted 2025-2026 weekly OOS result, but with high cash weight. Treat it as a challenger, not a promoted operating default:

```bash
.venv/bin/python benchmark.py \
  --config config/config.yaml \
  --train-test \
  --train-start 2023-07-11 \
  --train-end 2024-12-20 \
  --test-start 2025-01-16 \
  --test-end 2026-07-06 \
  --top-n 5 \
  --period 5y \
  --frequency weekly \
  --random-baseline 1000 \
  --bootstrap 1000 \
  --train-top-k-rules 5 \
  --historical-rule-prior-limit 5 \
  --historical-rule-prior-lookback 50 \
  --historical-rule-prior-root models/candidates \
  --rank-mode decision \
  --xgb-blend-weight 0.0 \
  --trade-sim \
  --min-dollar-volume 10000000 \
  --min-price 5 \
  --max-gap-open 0.08 \
  --entry-penalty-bps 20 \
  --adverse-sector-skip NLR,XLK
```

Latest controlled result:

- `reports/benchmark_train_test_20260712_144040` used adjusted weekly split `2023-07-11..2024-12-20` -> `2025-01-16..2026-07-06` with `--adverse-sector-skip NLR,XLK`.
- Best OOS rule: historical prior TP 6%, SL 4%, Hold 7D.
- OOS portfolio mean: `0.392%`, random mean: `0.113%`, alpha: `0.279%`, p-value: `0.042`, MDD: `7.26%`, active trades: `243/380`.
- A later conditional score filter run (`--adverse-conditional-sector-skip NLR,XLK --adverse-conditional-max-rank-score 82`) improved 2025-2026 test trade results, but train-side alpha remained slightly negative.

For auto-cycle trials, keep these env vars empty by default. If explicitly testing this challenger, set `PHOENIX_ADVERSE_SECTOR_SKIP=NLR,XLK` in the host-local `config/phoenix_auto_cycle.env` and require rolling OOS before promotion.


## Rule Candidate Memory

The auto cycle evaluates the latest train-grid rules plus a small set of recent historical OOS rule priors. This keeps comparatively better TP/SL/Hold combinations, such as a rule that was less bad in a prior 2025-2026 split, in the challenger pool instead of repeatedly forgetting them when the latest train-only grid changes.

Relevant env settings:

```bash
PHOENIX_HISTORICAL_RULE_PRIOR_LIMIT=5
PHOENIX_HISTORICAL_RULE_PRIOR_LOOKBACK=50
PHOENIX_HISTORICAL_RULE_PRIOR_ROOT=models/candidates
```

This does not weaken promotion gates. Candidates still need alpha, p-value, leakage audit, and rolling OOS checks to pass before promotion.

## Feedback Capture

Use `scripts/phoenix_add_feedback.py` to append structured feedback without editing CSV by hand:

```bash
./scripts/phoenix_add_feedback.py \
  --as-of 2026-07-09 \
  --ticker NVDA \
  --source telegram_top \
  --rank 1 \
  --decision watch \
  --label good \
  --return-5d 4.5% \
  --reason trend_following \
  --market-context "semis strong" \
  --notes "clean setup"
```

The script writes to `data/operator_feedback.csv` by default. `config/operator_feedback.csv.example` documents the schema.

Recommended labels:

- `good`: candidate behavior was useful and aligned with the thesis.
- `bad`: candidate failed in a way the system should learn to avoid.
- `neutral`: not enough signal or mixed outcome.
- `skip`: candidate looked invalid before observation.

Recommended reason categories:

- `trend_following`
- `market_regime`
- `sector_context`
- `earnings_or_event`
- `liquidity_gap`
- `overextended`
- `false_similarity`
- `risk_warning_missing`
- `other`

Summarize feedback:

```bash
./scripts/phoenix_feedback_summary.py --feedback-csv data/operator_feedback.csv
```

Fill empty feedback forward returns from cached daily OHLCV data:

```bash
.venv/bin/python scripts/phoenix_update_feedback_returns.py \
  --feedback-csv data/operator_feedback.csv \
  --cache-dir data
```

For unattended collection while auto-learning is paused, install the dedicated user timer:

```bash
cp systemd/phoenix-feedback-return-cycle.service ~/.config/systemd/user/
cp systemd/phoenix-feedback-return-cycle.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now phoenix-feedback-return-cycle.timer
```

This timer runs `scripts/phoenix_feedback_return_cycle.sh` daily after the US close should be available in Korea time. It refreshes daily OHLCV with `scripts/fetch_daily_data.py --refresh`, then fills feedback returns and prints a feedback summary. It is separate from `phoenix-auto-cycle.timer`, so creating `.phoenix_auto_cycle.pause` stops model validation/promotion but does not stop daily feedback-return upkeep.

## Promotion Policy

Do not weaken promotion thresholds just to get more frequent promotions. If candidates keep failing, inspect the failure reasons first.

Current conservative defaults:

- Decision-only ranking is promotable by default.
- XGB/ranking promotion is blocked unless explicitly allowed.
- Leakage audit is required.
- Rolling OOS can be required for stricter promotion.
- Telegram bot restarts only after successful promotion.

## Stateful Shadow Portfolio

`phoenix-shadow-portfolio.timer` advances a research-only SQLite portfolio
every ten minutes during the U.S. session. It never imports a broker or
account client and cannot place live orders.

The worker consumes only prospective `yfinance` rows dated on or after
2026-07-29. Entry quotes and exit bars both use completed `YFINANCE_1M`
bars. A signal is permanently idempotent by its ticker, recorded timestamp,
context timestamp, and source.

Operational checks:

```bash
systemctl --user status phoenix-shadow-portfolio.timer
.venv/bin/python scripts/phoenix_shadow_portfolio_status.py --json
.venv/bin/python scripts/phoenix_shadow_ledger_validation.py --json
```

State is stored at:

```text
data/research/paper_shadow_portfolio/shadow_portfolio_v1.sqlite3
```

The database is a runtime research artifact. Do not copy it into model
features or use it to retrain the Champion. Live review remains blocked until
the frozen sample, duration, net-return, drawdown, quote-rejection, calibration,
regime, and manual-approval gates all pass.

## Next Improvement Loop

1. Collect feedback for recent Telegram `/top` candidates.
2. Run `phoenix_feedback_summary.py`.
3. Compare feedback categories with rejected/promoted candidate metrics.
4. Convert repeated failure modes into a feature, filter, or gate.
5. Validate each change independently with purged train/test and rolling OOS.
