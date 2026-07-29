# Phoenix Adjusted Weekly Benchmark Handoff - 2026-07-11

Audience: next Codex agent continuing this repo. This records the continuation after `docs/handoff/2026-07-11-adjusted-window-benchmark-handoff.md`.

## What Was Continued

The prior handoff recommended a weekly-frequency run on the same adjusted split if experiments continued:

```text
train: 2023-07-11..2024-12-20
test:  2025-01-16..2026-07-06
```

The user said "그래 가보자", so the adjusted coverage audit was repeated and the weekly benchmark was run.

## Coverage Audit Recheck

Command:

```bash
.venv/bin/python scripts/phoenix_data_coverage_audit.py --include-etfs \
  --split adjusted_main:2023-07-11,2024-12-20,2025-01-16,2026-07-06
```

Output:

```text
reports/data_coverage/20260711_204241
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

The adjusted split is still the honest split for this cache. The configured main split remains unusable for current individual-equity cache coverage.

## Adjusted Weekly Benchmark

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

Output:

```text
reports/benchmark_20260711_204301
reports/benchmark_20260711_212634
reports/benchmark_train_test_20260711_233726
```

The run took a long time because one or more host auto-cycle jobs kept running concurrently and could not be killed from this session.

Best OOS fixed-rule result:

```text
#1 historical_prior_20260711_052933 TP 6% / SL 4% / Hold 7D
OOS Portfolio -0.1%
Random 0.1%
Alpha -0.1%
p=0.7532
MDD 21.7%
cash 2.4%
active trades 371/380
test dates 76
```

Other selected rules were also weak:

```text
train_grid_rank_1 TP 8% / SL 4% / Hold 10D: alpha -0.3%, p=0.8711, MDD 29.2%
train_grid_rank_2 TP 6% / SL 4% / Hold 10D: alpha -0.2%, p=0.8721, MDD 24.7%
historical_prior TP 8% / SL 3% / Hold 7D: alpha -0.3%, p=0.9500, MDD 31.2%
train_grid_rank_5 TP 8% / SL 3% / Hold 10D: alpha -0.5%, p=0.9920, MDD 33.4%
```

## Interpretation

Weekly frequency increased sample size from the prior monthly adjusted run, but did not improve OOS quality. It made the risk picture worse:

- all evaluated fixed rules had negative OOS alpha;
- p-values remained weak;
- best MDD rose to 21.7%, with several rules near or above 30%;
- train grid looked acceptable in-sample, but did not transfer OOS.

This strengthens the prior conclusion: poor OOS behavior is not explained only by the old missing-history train-window issue. Do not promote this result and do not relax gates.

## Important Runtime Note

While this manual weekly run was executing, host auto-cycle jobs repeatedly ran in parallel with the old configured windows. Examples seen:

```text
models/candidates/20260711_194751
models/candidates/20260711_211338
models/candidates/20260711_225125
```

These auto-cycle runs use configured main/rolling windows such as:

```text
2022-01-03..2023-12-15 -> 2024-01-08..2024-12-20
2023-01-01..2024-12-20 -> 2025-01-16..2026-07-06
```

Those windows are still known to have bad train coverage with the current cache. Treat their results as low-confidence unless coverage is fixed or the auto-cycle env is explicitly realigned after a successful experiment.

## Recommended Next Step

Stop tuning TP/SL/Hold as the primary lever for now.

Next work should be signal/ranking diagnosis:

1. Compare top picks versus random by sector, regime, date bucket, and drawdown period.
2. Inspect high-score losing names from `reports/benchmark_train_test_20260711_233726` and `reports/benchmark_20260711_212634`.
3. Use `scripts/phoenix_adverse_excursion_diagnostic.py` to identify segments that systematically stop out or draw down.
4. Use `scripts/phoenix_risk_filter_experiment.py` only as an offline OOS experiment, not as a promotion shortcut.
5. Decide whether to backfill individual-equity history from a better data source before continuing rolling split work.

Do not update host-local `config/phoenix_auto_cycle.env` from this result. If anything, the host auto-cycle should be paused or moved away from the known-bad train windows before further unattended runs.

## Current Worktree Notes

This handoff was added on top of the existing dirty worktree. Do not revert unrelated changes.

Expected relevant dirty/untracked files still include:

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
?? docs/handoff/2026-07-11-adjusted-weekly-benchmark-handoff.md
?? scripts/phoenix_adverse_excursion_diagnostic.py
?? scripts/phoenix_data_coverage_audit.py
?? scripts/phoenix_risk_filter_experiment.py
```

## Follow-Up Signal/Risk Diagnostics

After the weekly run, adverse excursion and offline risk-filter diagnostics were run against:

```text
reports/benchmark_20260711_212634
```

Adverse excursion output:

```text
reports/adverse_excursion/20260711_adjusted_weekly
```

Summary:

```text
active_trades=371
take_profit_trades=123
stop_loss_trades=228
time_exit_trades=20
active_win_rate=36.9%
active_avg_return=-0.0313%
active_median_return=-4.33%
stop_loss_rate=61.46%
take_profit_rate=33.15%
```

Worst larger categorical segments:

```text
NLR: 59 trades, avg -2.03%, SL 79.7%, TP 18.6%
XLK: 68 trades, avg -0.85%, SL 69.1%, TP 26.5%
URA: 50 trades, avg -0.31%, SL 64.0%, TP 32.0%
Bear Trend: 10 trades, avg -3.13%, SL 90.0%, TP 10.0%
Broad Bull: 20 trades, avg -1.47%, SL 70.0%, TP 20.0%
Neutral / Mixed: 118 trades, avg -0.45%, SL 64.4%, TP 29.7%
```

Better segments:

```text
SOXX: 105 trades, avg +1.03%, SL 54.3%, TP 42.9%
XLU: 45 trades, avg +1.50%, SL 42.2%, TP 42.2%
XLI: 14 trades, avg +1.80%, SL 42.9%, TP 42.9%
Risk Off: 42 trades, avg +1.07%, SL 52.4%, TP 42.9%
Narrow Tech Rotation: 59 trades, avg +1.20%, SL 50.8%, TP 42.4%
```

Risk-filter experiment output:

```text
reports/risk_filter_experiment/20260711_adjusted_weekly
```

Note: the first attempt with `--hold-days 7` failed because the ranked-detail CSV has 5D/10D forward-return columns but not 7D columns.

Actual top5 skip-to-cash result:

```text
baseline:
  mean -0.0305%
  MDD 29.24%
  SL 61.46%
  cash 2.37%

hard_skip_nlr_only:
  mean +0.2841%
  MDD 12.02%
  SL 58.01%
  cash 17.89%

hard_skip_nlr_xlk:
  mean +0.4354%
  MDD 7.97%
  SL 54.92%
  cash 35.79%

hard_skip_bear_nlr_xlk:
  mean +0.4835%
  MDD 7.97%
  SL 54.01%
  cash 37.63%
```

Interpretation:

- Hard skip / cash treatment looks much more promising than soft rerank penalties in this offline screen.
- `NLR` alone is the cleanest first candidate because it improves mean and MDD with less cash than `NLR,XLK`.
- `NLR,XLK` and `Bear Trend,NLR,XLK` improve risk more, but cash weight around 36-38% is operationally large.
- Proxy rerank results remained poor, so do not treat the penalty/rerank layer as validated.

Recommended next validation:

1. Run a controlled OOS benchmark with `--adverse-sector-skip NLR` only.
2. If it improves OOS without excessive cash, compare against `--adverse-sector-skip NLR,XLK`.
3. Only then test `--adverse-regime-skip "Bear Trend"` in combination.
4. Do not promote these skips from offline diagnostics alone.

## 2026-07-12 controlled NLR skip OOS validation

Command completed:

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
  --entry-penalty-bps 20 \
  --adverse-sector-skip NLR
```

Outputs:

- Train report: `reports/benchmark_20260711_235324`
- Test report: `reports/benchmark_20260712_002706`
- Train/test OOS: `reports/benchmark_train_test_20260712_015950`

Result:

- Controlled skip worked mechanically: OOS active trades dropped from 371 to 312 and cash slots rose from 9 to 68 (`cash_weight_mean` 2.37% -> 17.89%).
- Best OOS rule with NLR skip was the historical prior TP 6% / SL 4% / Hold 7D:
  - portfolio mean 0.168%
  - random mean 0.100%
  - alpha +0.068%
  - p=0.3816
  - MDD 10.63%
  - active trades 312/380
- Train grid rank 1 TP 8% / SL 4% / Hold 10D was essentially random:
  - portfolio mean 0.284%
  - random mean 0.285%
  - alpha -0.001%
  - p=0.5025
  - MDD 12.02%
- Compared with the no-skip weekly OOS (`reports/benchmark_train_test_20260711_233726`), NLR skip materially reduced MDD for the best prior rule (21.70% -> 10.63%) and made alpha positive, but the p-value stayed weak and the result did not robustly beat random.

Interpretation:

- The offline adverse-excursion diagnostic was directionally useful for drawdown reduction, but not strong enough as a standalone alpha filter.
- Do not promote NLR skip directly. Treat it as a risk overlay candidate that needs either a stronger regime/sector interaction rule or a validation with fewer random-baseline false positives.
- Next candidate is not broader blind skipping by default. If continuing, test `NLR,XLK` only as a drawdown-control experiment and require positive alpha with p-value improvement, not just lower MDD.

## Recommended next direction

The NLR controlled OOS validation reduced drawdown but did not prove robust alpha. The next work should avoid broad TP/SL/Hold tuning and avoid promoting sector skips from diagnostics alone.

Recommended path:

1. Treat NLR skip as a risk overlay candidate, not an alpha source.
   - Keep the result because MDD improved materially.
   - Do not enable it by default unless a second validation also improves alpha and p-value.
   - Use it only in experiments where drawdown reduction is the explicit objective.

2. Run one controlled `NLR,XLK` skip experiment, but with stricter acceptance criteria.
   - Same adjusted weekly window: train `2023-07-11..2024-12-20`, test `2025-01-16..2026-07-06`.
   - Same benchmark flags as the NLR-only run, changing only `--adverse-sector-skip NLR,XLK`.
   - Accept only if all are true:
     - portfolio alpha is positive,
     - p-value improves versus NLR-only `0.3816`,
     - MDD stays below the no-skip baseline `21.70%`,
     - cash weight does not become so high that the model is mostly avoiding trades.
   - Reject if it only lowers MDD while alpha/p-value weaken.

3. If `NLR,XLK` fails, stop sector skip expansion.
   - Do not test broad sector exclusion lists.
   - Move to conditional risk gating instead: skip a sector only under specific regime/score conditions.
   - Candidate rule shape: `sector in {NLR, XLK}` AND `regime is Risk Off/Bear/Neutral weak` AND `score bucket below threshold`.

4. Use offline diagnostics to design conditional gates, then validate with full OOS.
   - Diagnostics can rank suspicious segments.
   - Promotion requires full train/test OOS, not post-hoc segment returns.
   - Every candidate must be compared to random baseline and no-skip baseline.

5. Start investigating ranking quality, not exit tuning.
   - TP/SL/Hold grid changes have repeatedly failed to produce robust OOS alpha.
   - The more likely issue is entry selection/ranking under adverse regimes.
   - Next diagnostics should ask: which selected names were worse than random names from the same date/sector/regime, and what features separated them?

6. Practical next command if continuing immediately:

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
  --entry-penalty-bps 20 \
  --adverse-sector-skip NLR,XLK
```

Decision rule after that run:

- If `NLR,XLK` beats NLR-only on alpha and p-value while keeping MDD controlled, test a conditional version next.
- If it only improves MDD, keep it as a defensive overlay candidate but do not promote.
- If it worsens alpha or cash weight becomes excessive, stop skip-list expansion and pivot to ranking diagnostics.

## 2026-07-12 controlled NLR,XLK skip OOS validation

Command completed:

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
  --entry-penalty-bps 20 \
  --adverse-sector-skip NLR,XLK
```

Outputs:

- Train report: `reports/benchmark_20260712_115905`
- Test report: `reports/benchmark_20260712_123944`
- Train/test OOS: `reports/benchmark_train_test_20260712_144040`

Runtime note:

- Host root-owned auto-cycle jobs kept restarting during this manual run.
- This session could not kill them: plain `kill` returned `Operation not permitted`, and `sudo -n kill` required interactive authentication.
- The manual run still completed, but runtime was much longer than normal.
- The auto-cycle jobs continued using the known-bad configured train start `2023-01-01`; treat those auto-cycle outputs as low-confidence unless separately audited.

Result:

- Best OOS rule was again the historical prior TP 6% / SL 4% / Hold 7D:
  - portfolio mean 0.3922%
  - random mean 0.1133%
  - alpha +0.2789%
  - p=0.0420
  - MDD 7.26%
  - active trades 243/380
  - cash slots 137/380
  - cash weight 36.05%
- Best train-grid rule was TP 6% / SL 4% / Hold 10D:
  - portfolio mean 0.3086%
  - random mean 0.1244%
  - alpha +0.1842%
  - p=0.1489
  - MDD 8.15%
  - active trades 243/380
  - cash weight 36.05%

Comparison versus prior weekly validations:

- No-skip weekly best prior (`reports/benchmark_train_test_20260711_233726`):
  - alpha -0.1%, p=0.7532, MDD 21.7%, cash 2.4%, active trades 371/380.
- NLR-only weekly best prior (`reports/benchmark_train_test_20260712_015950`):
  - alpha +0.0676%, p=0.3816, MDD 10.63%, cash 17.89%, active trades 312/380.
- NLR,XLK weekly best prior (`reports/benchmark_train_test_20260712_144040`):
  - alpha +0.2789%, p=0.0420, MDD 7.26%, cash 36.05%, active trades 243/380.

Interpretation:

- `NLR,XLK` is the first controlled adjusted-weekly experiment here that clearly improves alpha, p-value, and MDD versus both no-skip and NLR-only.
- The result is promising as a defensive risk overlay, but the cash weight is high: about 36% of top-5 slots are skipped to cash.
- Do not promote `NLR,XLK` as an unconditional production default yet. It may be avoiding a large part of the opportunity set, not only filtering bad trades.
- Do not expand the hard skip list beyond `NLR,XLK` as the next move.

Recommended next step:

1. Design a conditional `NLR,XLK` risk gate instead of broader unconditional skips.
2. Candidate rule shape:
   - skip sector in `{NLR, XLK}`;
   - only when regime or score context is adverse;
   - examples to test offline first: `regime in {Neutral / Mixed, Broad Bull, Bear Trend}` or low/medium score buckets.
3. Use diagnostics to choose one or two conditional gates, then validate with full train/test OOS.
4. Acceptance should require:
   - positive alpha;
   - p-value competitive with or better than 0.0420, or at least clearly better than NLR-only 0.3816 if cash falls materially;
   - MDD below no-skip 21.70%;
   - cash weight materially below 36.05% if possible.
5. Add an explicit pause/disable guard to `scripts/phoenix_auto_cycle.sh` or host env before long future manual experiments, because root auto-cycle currently restarts and competes for CPU while using known-bad windows.

## 2026-07-12 auto-cycle pause guard

After the `NLR,XLK` run, a small operational guard was added because root-owned scheduled auto-cycle jobs kept restarting during manual validation and used the known-bad configured train start `2023-01-01`.

Changes:

- `scripts/phoenix_auto_cycle.sh` now exits early if either condition is true:
  - `PHOENIX_AUTO_CYCLE_DISABLED=1`
  - the pause file exists at `PHOENIX_PAUSE_FILE`, defaulting to `.phoenix_auto_cycle.pause` under the repo root.
- `config/phoenix_auto_cycle.env.example` documents `PHOENIX_AUTO_CYCLE_DISABLED` and `PHOENIX_PAUSE_FILE`.
- `docs/operations/phoenix-auto-learning-ops.md` documents how to pause and resume scheduled cycles.
- `.phoenix_auto_cycle.pause` was created in this workspace so future scheduled cycles should exit early after they pick up the updated script.

Verification:

```bash
bash -n scripts/phoenix_auto_cycle.sh
```

Result: passed.

Resume note:

- To resume scheduled auto-cycle, remove `.phoenix_auto_cycle.pause` or point `PHOENIX_PAUSE_FILE` elsewhere and ensure `PHOENIX_AUTO_CYCLE_DISABLED=0`.
- Already-running root-owned jobs may still need to finish, because this session could not terminate them without interactive sudo.


## 2026-07-12 conditional NLR,XLK score-gate continuation

Continuation after an interrupted run. The interrupted work was the conditional `NLR,XLK` risk gate experiment using the adjusted weekly split.

Important implementation fix found during continuation:

- `benchmark.py` was applying adverse sector/regime filters to the base trade simulation and random trade cache, but not to the train `grid_search` call.
- This meant `reports/benchmark_20260712_161904` initially had a conditional-gated trade summary but an ungated train grid.
- Fixed `run_benchmark()` so `_grid_search_trade_rules()` receives `adverse_sector_skip`, `adverse_regime_skip`, `adverse_conditional_sector_skip`, `adverse_conditional_regime_skip`, and `adverse_conditional_max_rank_score`.
- Re-ran the train report with `--resume-dir reports/benchmark_20260712_161904`; all ranking dates were skipped from partials and the grid was recomputed with the conditional gate.

Verification run completed:

```bash
.venv/bin/python benchmark.py \
  --config config/config.yaml \
  --start 2023-07-11 \
  --end 2024-12-20 \
  --top-n 5 \
  --period 5y \
  --frequency weekly \
  --random-baseline 1000 \
  --bootstrap 1000 \
  --rank-mode decision \
  --xgb-blend-weight 0.0 \
  --trade-sim \
  --grid-search \
  --min-dollar-volume 10000000 \
  --min-price 5 \
  --max-gap-open 0.08 \
  --entry-penalty-bps 20 \
  --adverse-conditional-sector-skip NLR,XLK \
  --adverse-conditional-max-rank-score 82 \
  --resume-dir reports/benchmark_20260712_161904
```

Corrected train grid top rule:

```text
#1 TP 8% / SL 4% / Hold 10D
active 192/295
cash 103/295, cash weight 34.9%
conditional sector filtered 91
portfolio mean 0.630%
portfolio MDD 9.80%
```

Interrupted test benchmark was resumed and completed:

```bash
.venv/bin/python benchmark.py \
  --config config/config.yaml \
  --start 2025-01-16 \
  --end 2026-07-06 \
  --top-n 5 \
  --period 5y \
  --frequency weekly \
  --random-baseline 1000 \
  --bootstrap 1000 \
  --rank-mode decision \
  --xgb-blend-weight 0.0 \
  --trade-sim \
  --take-profit 0.08 \
  --stop-loss 0.04 \
  --hold-days 10 \
  --min-dollar-volume 10000000 \
  --min-price 5 \
  --max-gap-open 0.08 \
  --entry-penalty-bps 20 \
  --adverse-conditional-sector-skip NLR,XLK \
  --adverse-conditional-max-rank-score 82 \
  --resume-dir reports/benchmark_20260712_165217
```

Output:

```text
reports/benchmark_20260712_165217
```

Test result for corrected train-grid #1 rule:

```text
TP 8% / SL 4% / Hold 10D
active 245/380
cash 135/380, cash weight 35.5%
conditional sector filtered 127
portfolio mean 0.456%
random portfolio mean 0.227%
alpha +0.228%
p=0.1169
portfolio MDD 6.55%
```

Interpretation:

- Conditional `NLR,XLK` with `final_rank_score <= 82` does improve OOS mean and MDD versus no-skip and NLR-only, but it does not improve enough versus unconditional `NLR,XLK`.
- Cash weight remains high at 35.5%, almost the same as unconditional `NLR,XLK` at about 36.1%.
- p-value is weaker than unconditional `NLR,XLK` (`0.1169` vs `0.0420`).
- Therefore this specific score-threshold conditional gate should not be promoted. It failed the intended goal of materially reducing cash while retaining the stronger p-value.

Recommended next step:

- Do not expand the hard skip list.
- Do not promote unconditional or score-threshold `NLR,XLK` yet.
- If continuing conditional gates, test regime-conditioned gates instead of score-only gates, because score-only kept nearly the same cash burden. Candidate shape: `sector in {NLR, XLK}` AND `regime in {Neutral / Mixed, Broad Bull, Bear Trend, Risk Off}`.
- Before another long run, keep `.phoenix_auto_cycle.pause` in place unless scheduled auto-cycle competition is desired.

## 2026-07-13 regime-conditioned `NLR,XLK` follow-up

The next follow-up expanded `scripts/phoenix_risk_filter_experiment.py` with regime-specific screen masks for `NLR,XLK`, including `risk_off`, `ai_growth_rotation`, `narrow_tech_rotation`, `nlr_xlk_broad_bull`, `nlr_xlk_bear_trend`, `nlr_xlk_risk_off`, `nlr_xlk_ai_growth`, `nlr_xlk_narrow_tech`, and combined risk/bear variants.

Offline screen command:

```bash
.venv/bin/python scripts/phoenix_risk_filter_experiment.py \
  --benchmark-dir reports/benchmark_20260711_212634 \
  --take-profit 0.06 \
  --stop-loss 0.04 \
  --hold-days 10 \
  --output-dir reports/risk_filter_experiment/20260713_regime_nlr_xlk_screen
```

Screen output:

```text
reports/risk_filter_experiment/20260713_regime_nlr_xlk_screen
```

The best raw screen remained high-cash `hard_skip_bear_nlr_xlk`. The practical lower-cash candidate was `conditional_skip_nlr_xlk_ai_growth`:

```text
active 326/380
cash 54/380, cash weight 14.2%
portfolio mean 0.265%
portfolio MDD 20.33%
```

Full purged train/test validation was then run with conditional `NLR,XLK` skip only in `AI Growth Rotation`:

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
  --entry-penalty-bps 20 \
  --adverse-conditional-sector-skip NLR,XLK \
  --adverse-conditional-regime-skip "AI Growth Rotation"
```

Outputs:

```text
train report: reports/benchmark_20260713_090857
test report: reports/benchmark_20260713_094156
train/test summary: reports/benchmark_train_test_20260713_111247
```

Fixed-rule OOS highlights:

```text
#1 historical_prior_20260711_052933_TP0.0600_SL0.0400_H7
portfolio mean 0.193%
random mean 0.087%
alpha +0.105%
p=0.2877
MDD 13.96%
cash 14.21%
active 326/380

#2 train_grid_rank_1 TP 8% / SL 4% / Hold 10D
portfolio mean 0.265%
random mean 0.230%
alpha +0.035%
p=0.4346
MDD 20.33%
cash 14.21%
active 326/380
```

Interpretation:

- The `AI Growth Rotation` conditional gate materially reduced cash versus unconditional `NLR,XLK` and score-threshold `NLR,XLK` at about 14.2% vs about 35-36%.
- It did not preserve the stronger statistical profile. Best fixed-rule p-value was only `0.2877`, and train-grid #1 p-value was `0.4346`.
- MDD improved versus no-skip but was worse than unconditional/score-threshold `NLR,XLK`.
- Do not promote this gate to production. Keep it as a lower-cash research candidate only.

Validation:

```text
.venv/bin/python -m py_compile scripts/phoenix_risk_filter_experiment.py benchmark.py scripts/phoenix_rolling_oos.py scripts/phoenix_data_coverage_audit.py scripts/phoenix_adverse_excursion_diagnostic.py
.venv/bin/python tests/test_core_synthetic.py
```

Both passed. The synthetic test required rerun outside the sandbox because the sandbox failed with `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`.
