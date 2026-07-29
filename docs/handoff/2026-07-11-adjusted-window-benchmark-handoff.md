# Phoenix Adjusted Train Window Benchmark Handoff - 2026-07-11

Audience: next Codex agent continuing this repo. This records the continuation from `docs/handoff/2026-07-10-auto-learning-data-coverage-handoff.md`.

## What Was Continued

The prior handoff recommended testing whether aligning the train window to actual cached data coverage improves auto-learning OOS results.

Initial suggested train start was `2023-07-10`, but the current cache mostly starts on `2023-07-11`, so the benchmark was run with `2023-07-11`.

## Data Coverage Audit

Command:

```bash
.venv/bin/python scripts/phoenix_data_coverage_audit.py --include-etfs \
  --split adjusted_main:2023-07-11,2024-12-20,2025-01-16,2026-07-06
```

Output directory:

```text
reports/data_coverage/20260711_123037
```

Summary:

```text
tickers=120 missing=0 short_history=2 stale=0

adjusted_main train 2023-07-11..2024-12-20:
  usable 115/120 (95.8%), required_days=367
  failures: starts_after_window=5, coverage<0.80=4

adjusted_main test 2025-01-16..2026-07-06:
  usable 118/120 (98.3%), required_days=367
  failures: starts_after_window=2

main train 2023-01-02..2024-12-20:
  usable 0/120 (0.0%), required_days=515
  failures: coverage<0.80=120, starts_after_window=120
```

Important note:

- `2023-07-10` is still too early for this cache because 114 tickers currently start on `2023-07-11`.
- With `2023-07-10`, audit output was `usable 1/120 (0.8%)` for adjusted train.
- With `2023-07-11`, coverage is good enough for a controlled benchmark.

## Adjusted Monthly Benchmark

Command:

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
  --frequency monthly \
  --random-baseline 1000 \
  --bootstrap 1000 \
  --train-top-k-rules 5 \
  --historical-rule-prior-limit 5 \
  --historical-rule-prior-lookback 50 \
  --historical-rule-prior-root models/candidates \
  --rank-mode decision \
  --xgb-blend-weight 0.0 \
  --embargo-trading-days 10 \
  --trade-sim \
  --min-dollar-volume 10000000 \
  --min-price 5 \
  --max-gap-open 0.08 \
  --entry-penalty-bps 20
```

Output directories:

```text
reports/benchmark_20260711_123051
reports/benchmark_20260711_124112
reports/benchmark_train_test_20260711_132146
```

Summary CSVs:

```text
reports/benchmark_train_test_20260711_132146/benchmark_train_test_summary.csv
reports/benchmark_train_test_20260711_132146/benchmark_oos_rules.csv
reports/benchmark_train_test_20260711_132146/benchmark_train_grid_search.csv
```

Best OOS fixed-rule result:

```text
#1 train_grid_rank_4 TP 6% / SL 4% / Hold 7D
OOS Portfolio 0.4%
Random 0.6%
Alpha -0.2%
p=0.6623
MDD 5.1%
cash 6.7%
active trades 84/90
```

Other selected/prior rules also had negative OOS alpha:

```text
train_grid_rank_1 TP 8% / SL 4% / Hold 7D: alpha -0.5%, p=0.8591
historical_prior TP 5% / SL 3% / Hold 5D: alpha -0.4%, p=0.8641
historical_prior TP 8% / SL 3% / Hold 10D: alpha -0.8%, p=0.9500
```

## Interpretation

Aligning the train start to actual data coverage fixed the coverage bottleneck for this controlled run, but it did not materially improve monthly OOS performance.

Do not update `config/phoenix_auto_cycle.env` to the adjusted train start as a promotion-oriented operational change based on this result alone. The best adjusted-window result is still below random baseline and has a weak p-value.

## Recommended Next Step

The data/split issue is now clearer:

1. Keep `2023-07-11` as the honest earliest start for experiments that use the current cache.
2. Do not relax promotion gates.
3. Next pragmatic experiment is weekly frequency on the same adjusted split to increase sample size, but expect a longer runtime than the monthly run.
4. If weekly also fails, focus on signal/ranking quality or universe/data-source changes rather than TP/SL/Hold tuning.

Current dirty worktree still includes changes that predate this handoff plus the new data audit script and handoff docs. Do not revert unrelated changes.

## Session Closeout Notes

User instruction at closeout:

```text
"아냐 지금까지 기록 앞으로 할것 다 handoff에 넣고 마무리해"
```

Stop here. Do not start another benchmark in this session unless the user explicitly asks.

What was done in this continuation:

- Read latest handoff: `docs/handoff/2026-07-10-auto-learning-data-coverage-handoff.md`.
- Confirmed the prior diagnosis that old train windows are not trustworthy with the current cache.
- Ran a coverage audit for the proposed `2023-07-10` adjusted start and found it was still misaligned.
- Inspected current cached CSV first-date distribution and found most tickers start on `2023-07-11`.
- Re-ran coverage audit with `2023-07-11`, which made the adjusted train split usable.
- Ran the full monthly adjusted train/test benchmark with the handoff's conservative settings.
- Did not edit operational `config/phoenix_auto_cycle.env`, because adjusted-window OOS results did not justify it.
- Added this handoff file to preserve the outcome and next steps.

Important exact findings:

- `2023-07-10` adjusted train audit:
  - output: `reports/data_coverage/20260711_122953`
  - adjusted train usable: `1/120 (0.8%)`
  - cause: most tickers start after the requested window start.
- `2023-07-11` adjusted train audit:
  - output: `reports/data_coverage/20260711_123037`
  - adjusted train usable: `115/120 (95.8%)`
  - adjusted test usable: `118/120 (98.3%)`
- monthly adjusted benchmark:
  - output: `reports/benchmark_train_test_20260711_132146`
  - best OOS rule: `TP 6% / SL 4% / Hold 7D`
  - OOS portfolio: `0.4%`
  - random baseline: `0.6%`
  - alpha: `-0.2%`
  - p-value: `0.6623`
  - MDD: `5.1%`
  - active trades: `84/90`

Current interpretation:

- Data coverage was a real problem for earlier splits.
- Aligning to `2023-07-11` fixes most of the train-window coverage problem for the current cache.
- However, the adjusted monthly experiment still does not beat random baseline.
- Therefore, weak learning results are not explained only by missing train coverage.
- Do not weaken alpha, p-value, leakage, rolling, sample-size, or MDD gates as the next step.
- Do not promote or operationalize this adjusted monthly result.

Recommended future order:

1. If continuing experiments, run a weekly-frequency version of the same adjusted split to increase sample size:

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
     --embargo-trading-days 10 \
     --trade-sim \
     --min-dollar-volume 10000000 \
     --min-price 5 \
     --max-gap-open 0.08 \
     --entry-penalty-bps 20
   ```

2. Before interpreting that weekly run, repeat coverage audit with the same adjusted dates:

   ```bash
   .venv/bin/python scripts/phoenix_data_coverage_audit.py --include-etfs \
     --split adjusted_main:2023-07-11,2024-12-20,2025-01-16,2026-07-06
   ```

3. If weekly still has negative alpha or weak p-value, stop tuning TP/SL/Hold first. Move to signal/ranking diagnosis:
   - compare top picks versus random by sector/regime/date bucket;
   - inspect losing high-score names and whether final rank features are over-weighting noisy momentum;
   - use `scripts/phoenix_adverse_excursion_diagnostic.py` and `scripts/phoenix_risk_filter_experiment.py` if they are part of the current branch intent;
   - check whether adverse sector/regime skips improve OOS without creating too much cash weight.

4. If better long-history experiments are needed, choose a real historical data backfill source for individual equities instead of relying on yfinance `5y` cache alone.

5. Only if an adjusted or backfilled experiment produces positive OOS alpha with acceptable p-value, MDD, sample size, leakage audit, and rolling stability should host-local `config/phoenix_auto_cycle.env` be changed.

Known worktree state at closeout:

```text
 M benchmark.py
 M config/phoenix_auto_cycle.env.example
 M docs/operations/phoenix-auto-learning-ops.md
 M docs/operations/top-shadow-compare.md
 M phoenix_core/engines/ranking_engine.py
 M phoenix_core/services/telegram_command_bot.py
 M scripts/phoenix_auto_cycle.sh
 M scripts/phoenix_rolling_oos.py
 M tests/test_core_synthetic.py
?? docs/handoff/2026-07-10-auto-learning-data-coverage-handoff.md
?? docs/handoff/2026-07-11-adjusted-window-benchmark-handoff.md
?? scripts/phoenix_adverse_excursion_diagnostic.py
?? scripts/phoenix_data_coverage_audit.py
?? scripts/phoenix_risk_filter_experiment.py
```

Some modified files predate this continuation. Do not revert them unless the user explicitly asks.

At the final process check, the benchmark started in this continuation had completed. A separate `main.py --config config/config.yaml --top --top-n 5 --period 3y --refresh` process was visible and appeared to belong to another auto-cycle path, not this manual adjusted benchmark.
