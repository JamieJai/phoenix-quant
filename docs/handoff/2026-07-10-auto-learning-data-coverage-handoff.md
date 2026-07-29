# Phoenix Auto Learning / Data Coverage Handoff - 2026-07-10

Audience: next Codex agent continuing this exact repo. This is an agent-to-agent handoff. Be concrete, do not restart from scratch.

## User Intent

The user wants the Phoenix auto-learning loop tuned so useful results can accumulate over time. They specifically asked whether poor learning results could be due to insufficient stock data, then asked to proceed sequentially. This handoff records the current state so the next session can continue immediately.

## Current High-Level Diagnosis

The weak auto-learning results are not only a model/rule issue. Data coverage is a real bottleneck:

- The auto-learning train windows are configured around `2022-01-03..2023-12-15` and `2023-01-01..2024-12-20`.
- Before refresh, many cached CSVs started around `2023-07-10`, which made train coverage effectively unusable for the requested windows.
- After `5y` refresh, coverage improved but is still not sufficient for all configured train windows.
- Test windows are mostly fine. Train windows remain the problem.

Do not weaken promotion gates as the first response. The correct next work is to fix/align data coverage and split design before trusting OOS failure rates.

## Important Changes Already Made

### Auto-learning rule memory

Files changed:

- `benchmark.py`
- `scripts/phoenix_auto_cycle.sh`
- `scripts/phoenix_rolling_oos.py`
- `config/phoenix_auto_cycle.env.example`
- `docs/operations/phoenix-auto-learning-ops.md`

Behavior added:

- `benchmark.py` now supports historical OOS rule priors:
  - `--historical-rule-prior-limit`
  - `--historical-rule-prior-lookback`
  - `--historical-rule-prior-root`
- The auto cycle defaults to evaluating 5 historical OOS priors from `models/candidates`.
- Rolling OOS forwards the same options.
- This preserves comparatively better TP/SL/Hold combinations from prior OOS runs so the loop does not repeatedly forget less-bad rules when the latest train grid changes.
- This does not relax alpha/p-value/leakage/rolling promotion gates.

Verified historical priors currently selected:

- `TP 8% / SL 3% / Hold 10D`, historical alpha about `-0.163%`, p about `0.641`
- `TP 8% / SL 3% / Hold 7D`
- `TP 8% / SL 3% / Hold 5D`
- `TP 5% / SL 3% / Hold 5D`
- `TP 8% / SL 4% / Hold 10D`

### Telegram / display calibration from prior feedback

These changes were already in the worktree before this handoff work but are still relevant:

- `phoenix_core/engines/ranking_engine.py`
  - Display label now uses `final_rank_score` alignment instead of the unblended decision label.
  - Prevents cases like a high final score but visible label `제외`.
- `phoenix_core/services/telegram_command_bot.py`
  - `/hot` allows strong VWAP-missing moves when price move and risk are favorable.
- `tests/test_core_synthetic.py`
  - Has tests for display label and VWAP-missing hot candidate behavior.

### New data coverage audit

New file:

- `scripts/phoenix_data_coverage_audit.py`

Purpose:

- Reads cached `data/*.csv`; it does not download.
- Reports ticker-level coverage and split-level coverage.
- Writes:
  - `ticker_coverage.csv`
  - `split_coverage.csv`
  - `summary.json`
  under `reports/data_coverage/<timestamp>/`.

Important implementation detail:

- Required split windows are now calculated from requested dates, not clipped to the available SPY cache. This matters because SPY itself previously started late and was hiding missing history.

Documented in:

- `docs/operations/phoenix-auto-learning-ops.md`

## Commands Already Run

Syntax / regression:

```bash
.venv/bin/python -m py_compile scripts/phoenix_data_coverage_audit.py benchmark.py scripts/phoenix_rolling_oos.py
.venv/bin/python -m py_compile benchmark.py scripts/phoenix_rolling_oos.py scripts/phoenix_model_gate.py scripts/phoenix_auto_status.py
bash -n scripts/phoenix_auto_cycle.sh
.venv/bin/python tests/test_core_synthetic.py
```

All passed.

Data refresh:

```bash
.venv/bin/python scripts/fetch_daily_data.py --period 5y --refresh --manifest data/daily_data_manifest.csv
```

Result:

```text
downloading daily data: tickers=120 period=5y cache_dir=data refresh=True
success=120 failed_or_stale=0 manifest=data/daily_data_manifest.csv
latest_date_range=2026-07-09..2026-07-09
```

Coverage audit command:

```bash
.venv/bin/python scripts/phoenix_data_coverage_audit.py --include-etfs \
  --split split_2024:2022-01-03,2023-12-15,2024-01-08,2024-12-20 \
  --split split_2025_2026:2023-01-01,2024-12-20,2025-01-16,2026-07-06
```

Latest output directory:

```text
reports/data_coverage/20260710_103757
```

Latest coverage summary:

```text
tickers=120 missing=0 short_history=2 stale=0

split_2024 train 2022-01-03..2023-12-15:
  usable 76/120 (63.3%), required_days=492
  failures: starts_after_window=44, coverage<0.80=42

split_2024 test 2024-01-08..2024-12-20:
  usable 116/120 (96.7%), required_days=242
  failures: coverage<0.80=4, starts_after_window=4

split_2025_2026 train 2023-01-03..2024-12-20:
  usable 78/120 (65.0%), required_days=496
  failures: coverage<0.80=42, starts_after_window=42

split_2025_2026 test 2025-01-16..2026-07-06:
  usable 118/120 (98.3%), required_days=367
  failures: starts_after_window=2

main train 2023-01-03..2024-12-20:
  usable 78/120 (65.0%), required_days=496

main test 2025-01-16..2026-07-06:
  usable 118/120 (98.3%), required_days=367
```

Representative post-refresh dates:

```text
SPY  1254 rows 2021-07-12..2026-07-09
QQQ  1254 rows 2021-07-12..2026-07-09
NVDA  753 rows 2023-07-10..2026-07-09
CRWV  321 rows 2025-03-28..2026-07-09
SNDK  351 rows 2025-02-13..2026-07-09
```

Key finding:

- ETF history improved to 2021 after refresh.
- Many core individual tickers still start `2023-07-10`, including major names such as `MSFT`, `NVDA`, `AMD`, `MU`, `AMAT`, `ASML`, etc.
- Therefore, 2023-01 train windows still have only about 74.2% coverage for many core names, below the audit threshold of 80%.
- For the 2022-01 train split, those names only cover about 22.97% of required train days.

## Current Git State At Handoff

Expected modified/untracked files:

```text
 M benchmark.py
 M config/phoenix_auto_cycle.env.example
 M docs/operations/phoenix-auto-learning-ops.md
 M phoenix_core/engines/ranking_engine.py
 M phoenix_core/services/telegram_command_bot.py
 M scripts/phoenix_auto_cycle.sh
 M scripts/phoenix_rolling_oos.py
 M tests/test_core_synthetic.py
?? scripts/phoenix_data_coverage_audit.py
```

Notes:

- Some of these changes predate the data coverage work in this handoff, especially Telegram/ranking label changes.
- Do not revert unrelated user/previous-agent changes.
- `data/*.csv` refreshed but appears ignored/not shown in `git status`.
- `reports/data_coverage/*` generated but also not shown in `git status`, likely ignored.

## Next Recommended Step

Do not jump straight into more model complexity. Continue in this order:

1. Decide how to handle train-window coverage mismatch.
   - Option A: Move train starts forward to match actual individual equity history.
     - Example: `TRAIN_START=2023-07-10` or later.
     - Pros: honest coverage, faster, immediately testable.
     - Cons: shorter train history and fewer regimes.
   - Option B: Add/backfill a better historical data source for individual equities.
     - Needed because yfinance refresh still left many core names starting `2023-07-10`.
     - Pros: preserves intended rolling split design.
     - Cons: requires source decision and integration.
   - Option C: Keep split windows but make benchmark exclude/flag tickers with insufficient in-window coverage per split.
     - Pros: avoids misleading train data.
     - Cons: universe differs by split; must record it clearly in reports.

2. Short-term pragmatic recommendation:
   - First align train windows to actual cached data and run a controlled comparison.
   - Suggested experiment:

```bash
.venv/bin/python benchmark.py \
  --config config/config.yaml \
  --train-test \
  --train-start 2023-07-10 \
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

3. Then run the same coverage audit for the proposed adjusted windows before interpreting the benchmark:

```bash
.venv/bin/python scripts/phoenix_data_coverage_audit.py --include-etfs \
  --split adjusted_main:2023-07-10,2024-12-20,2025-01-16,2026-07-06
```

4. If adjusted-window benchmark improves materially, update `config/phoenix_auto_cycle.env` on the host, not just the example file.
   - The local `config/phoenix_auto_cycle.env` is gitignored and was not edited in this session.
   - Need to inspect/edit it explicitly if the user wants operational deployment.

5. After split alignment, consider weekly frequency experiment for sample size:
   - Monthly Top5 gives about 90 slots in OOS.
   - Weekly should increase sample size, but it also increases serial correlation; interpret p-values carefully.

6. Only after the data/split issue is handled should you expand universe or add model features.

## Watchouts For Next Agent

- Use `.venv/bin/python`, not system `python3`, for scripts needing pandas.
- `apply_patch` failed earlier because this environment’s sandbox produced `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`. If it still fails, use approved escalated commands carefully and keep edits scoped.
- The current auto cycle that started before the rule-prior change may still be running with old arguments. Verify with:

```bash
ps -ef | rg 'phoenix_auto_cycle|phoenix_rolling_oos|benchmark.py'
```

- Do not assume generated reports are tracked by git.
- Do not treat the latest poor OOS as purely strategy failure until data coverage and split definitions are aligned.

## Completion State

Completed:

- Added historical OOS rule memory to auto-learning candidate generation.
- Added data coverage audit tool.
- Refreshed 5y daily cache for 120 tickers successfully.
- Produced latest coverage report at `reports/data_coverage/20260710_103757`.
- Documented operation procedure.
- Ran syntax checks and synthetic regression test successfully.

Not completed:

- Did not change deployed `config/phoenix_auto_cycle.env`.
- Did not add external historical data source.
- Did not expand universe.
- Did not run weekly-frequency experiment.


## Continuation Results - 2026-07-10 12:20 KST

The next session did run the adjusted-window benchmark recommended above.

### Adjusted coverage audit

Command:

```bash
.venv/bin/python scripts/phoenix_data_coverage_audit.py --include-etfs \
  --split adjusted_main:2023-07-10,2024-12-20,2025-01-16,2026-07-06
```

Output directory:

```text
reports/data_coverage/20260710_105401
```

Summary:

- `adjusted_main train 2023-07-10..2024-12-20`: usable `115/120` tickers (`95.8%`), required_days `368`.
- `adjusted_main test 2025-01-16..2026-07-06`: usable `118/120` tickers (`98.3%`), required_days `367`.
- Remaining ticker issues: `SNDK` and `CRWV` have short history; several newer names still start after some windows.

Additional warmup-safe audit:

```bash
.venv/bin/python scripts/phoenix_data_coverage_audit.py --include-etfs \
  --split adjusted_warm:2023-11-01,2024-12-20,2025-01-16,2026-07-06
```

Output directory:

```text
reports/data_coverage/20260710_121812
```

Summary:

- `adjusted_warm train 2023-11-01..2024-12-20`: usable `116/120` tickers (`96.7%`), required_days `287`.
- `adjusted_warm test 2025-01-16..2026-07-06`: usable `118/120` tickers (`98.3%`), required_days `367`.

### Adjusted train-window benchmark

Command used:

```bash
.venv/bin/python benchmark.py \
  --config config/config.yaml \
  --train-test \
  --train-start 2023-07-10 \
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

Output:

```text
reports/benchmark_train_test_20260710_115009
train report: reports/benchmark_20260710_105417
test report:  reports/benchmark_20260710_110426
```

Important benchmark behavior:

- Train selected `18` monthly dates, but the first four failed:
  - `2023-07-10`: `PatternRecord가 너무 적습니다: 0`
  - `2023-08-01`: `PatternRecord가 너무 적습니다: 0`
  - `2023-09-01`: `PatternRecord가 너무 적습니다: 0`
  - `2023-10-02`: `PatternRecord가 너무 적습니다: 0`
- This means `2023-07-10` is a good raw coverage start, but not a good benchmark as-of start unless the benchmark has a warmup/history start separate from the scoring start.
- Effective train sample for grid search was `70` slots (`14` successful monthly dates x Top5), `65` active trades.
- Test had `19` selected monthly dates, then incomplete future windows were removed: detail `190 -> 180`, candidates `1887 -> 1787`, leaving `90` OOS slots.

Best OOS fixed rule from this adjusted run:

```text
#1 train_grid_rank_3
TP 8.0% / SL 2.0% / Hold 7D
n_slots=90 / n_active_trades=83 / cash_weight_mean=7.8%
portfolio_return_by_date_mean=0.092%
portfolio_random_mean=0.614%
portfolio_alpha=-0.522%
portfolio_p_value=0.8971
portfolio_mdd=8.44%
```

Interpretation:

- Moving the train start to match available cached data fixed the coverage problem, but did not improve OOS performance under this exact Top5/monthly/decision setup.
- Do not update deployed `config/phoenix_auto_cycle.env` to `TRAIN_START=2023-07-10` based on this run alone.
- The result points to a split-design issue too: benchmark needs warmup-aware as-of selection, or the benchmark start should move to about `2023-11-01` so early train dates have enough lookback.
- The old `reports/benchmark_train_test_20260709_101317` looked better, but it is not apples-to-apples: it used `top_n=10` and `rank_mode=both`, while this adjusted run used `top_n=5` and `rank_mode=decision`.

### Next Recommended Step After Continuation

1. Do not change operational env yet.
2. Run one apples-to-apples comparison when the current auto-cycle is not competing for CPU:

```bash
.venv/bin/python benchmark.py \
  --config config/config.yaml \
  --train-test \
  --train-start 2023-11-01 \
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

3. If `2023-11-01` still fails OOS, the next useful change is not loosening promotion gates. Prefer adding a benchmark/rolling-OOS warmup design, e.g. `data_start=2023-07-10` and `score_start=2023-11-01`, so training records can use warmup history while the scored train period remains explicit.


## Continuation Results - Weekly Frequency And Exit Diagnostics - 2026-07-10 16:55 KST

After the monthly `2023-11-01` warmup-safe run, a full weekly-frequency benchmark was run with the same split and Top5/decision settings.

Command:

```bash
.venv/bin/python benchmark.py \
  --config config/config.yaml \
  --train-test \
  --train-start 2023-11-01 \
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
reports/benchmark_train_test_20260710_164257
train report: reports/benchmark_20260710_140710
test report:  reports/benchmark_20260710_144536
```

Weekly run details:

- Train selected `60` weekly dates, no failed dates.
- Test selected `78` weekly dates.
- Incomplete future window removal: detail `780 -> 760`, candidates `7751 -> 7551`.
- Fixed-rule OOS evaluation used `n_slots=380`, `n_active_trades=372`, cash `2.1%`.

Best fixed OOS rule from weekly run:

```text
#1 train_grid_rank_1
TP 8.0% / SL 4.0% / Hold 10D
portfolio_return_by_date_mean=-0.042%
portfolio_random_mean=0.256%
portfolio_alpha=-0.298%
portfolio_p_value=0.8861
portfolio_mdd=27.96%
```

Interpretation:

- Weekly frequency increased sample size, but did not improve OOS. It was worse than monthly because drawdown expanded sharply.
- Do not change operational env based on weekly run.
- This makes it less likely that the main issue is only insufficient monthly sample size.

Useful diagnostic from weekly test report:

- Forward-return selection signal is not dead:
  - Top5 weekly test `avg_fwd_max_ret_5d=9.74%` vs random `6.04%`.
  - Top5 weekly test `avg_fwd_close_ret_5d=2.63%` vs random `1.41%`.
- But trade-sim conversion is poor:
  - best fixed rule median trade was about `-4.33%`.
  - stop-loss rate was about `61.6%`.
  - MDD was about `28%`.

Fast exit/entry diagnostic was run on existing weekly Top5 selected rows without rerunning ranking. Best observed variants:

```text
entry=close, same_day=take_first, TP=10%, SL=5%, Hold=10D: port=0.244%, MDD=21.82%
entry=next_open, same_day=take_first, TP=10%, SL=5%, Hold=10D: port=0.181%, MDD=25.70%
entry=next_open, same_day=stop_first, TP=10%, SL=5%, Hold=10D: port=0.141%, MDD=27.98%
```

Caution:

- `take_first` and `entry=close` are optimistic assumptions. Do not promote based on those.
- The only realistic diagnostic variant above is `next_open + stop_first + TP10/SL5/Hold10`, and even that still has too much MDD.
- This suggests the next bottleneck is not data coverage or frequency. It is the entry/exit conversion layer: the ranking signal has upside, but current TP/SL rules realize too many stop losses.

Next recommended step:

1. Keep operational env unchanged.
2. Do not loosen promotion gates.
3. Run a controlled train-selected expanded-grid experiment, preferably weekly first because monthly has only 90 OOS slots:

```bash
.venv/bin/python benchmark.py \
  --config config/config.yaml \
  --train-test \
  --train-start 2023-11-01 \
  --train-end 2024-12-20 \
  --test-start 2025-01-16 \
  --test-end 2026-07-06 \
  --top-n 5 \
  --period 5y \
  --frequency weekly \
  --random-baseline 1000 \
  --bootstrap 1000 \
  --train-top-k-rules 10 \
  --tp-list 0.05,0.06,0.08,0.10 \
  --sl-list 0.02,0.03,0.04,0.05 \
  --hold-list 5,7,10 \
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

4. If expanded realistic grid still fails, the next code-level work should be exit model design, not more data refresh. Candidate directions: volatility-scaled stops, time-based partial exit, or a separate sell/avoid model trained on adverse excursion.


## Continuation Results - Expanded Realistic Grid - 2026-07-10 19:55 KST

A controlled expanded-grid weekly run was completed after the exit diagnostics. It kept realistic execution assumptions: `entry_mode=next_open`, `same_day_rule=stop_first`, execution filters, and `entry_penalty_bps=20`.

Command highlights:

```text
train_start=2023-11-01
train_end=2024-12-20
test_start=2025-01-16
test_end=2026-07-06
frequency=weekly
top_n=5
train_top_k_rules=10
tp_list=0.05,0.06,0.08,0.10
sl_list=0.02,0.03,0.04,0.05
hold_list=5,7,10
rank_mode=decision
```

Output:

```text
reports/benchmark_train_test_20260710_195201
train report: reports/benchmark_20260710_170358
test report:  reports/benchmark_20260710_174326
```

Train grid result:

- Expanded grid did what it was supposed to do: train selected wider rules.
- Train grid rank 1: `TP10 / SL5 / Hold10`, in-sample portfolio mean about `0.960%`, train portfolio MDD about `23.4%`.
- Train grid rank 2: `TP10 / SL5 / Hold7`.

Best expanded-grid OOS fixed rule:

```text
#1 train_grid_rank_2
TP 10.0% / SL 5.0% / Hold 7D
n_slots=380 / n_active_trades=372 / cash=2.1%
portfolio_return_by_date_mean=0.1%
portfolio_random_mean=0.4%
portfolio_alpha=-0.3%
portfolio_p_value=0.8551
portfolio_mdd=30.2%
```

Other notable OOS rules:

```text
#2 TP10 / SL4 / Hold10: alpha about -0.3%, p=0.8801, MDD 27.8%
#4 TP10 / SL5 / Hold10: alpha about -0.3%, p=0.9001, MDD 28.0%
```

Interpretation:

- The wider grid improved raw OOS portfolio mean from negative to slightly positive for some rules, but it still underperformed random and drawdown remained too high.
- This confirms the problem is not simply that the old grid was too narrow.
- Do not deploy `TP10/SL5` or change promotion gates based on this run.
- The next useful work should be code/model design around adverse-excursion control, not more blind grid expansion.

Recommended next technical direction:

1. Add OOS diagnostics that rank selected candidates by adverse excursion features before trade simulation. Specifically compare winners vs stop-loss trades using:
   - `risk_score`
   - `pattern_rarity`
   - `hold_score`
   - `regime`
   - `sector_etf`
   - recent gap/open behavior
   - `fwd_min_ret_5d` and `fwd_min_ret_10d`
2. Add a risk-aware post-filter or reranker candidate and validate it OOS:
   - skip top picks with high predicted adverse excursion
   - reduce position/cash-slot high-risk picks
   - volatility-scaled stops instead of fixed percent stops
3. Only after this diagnostic/reranker layer improves OOS alpha and MDD should operational env be changed.

## Continuation Results - Adverse Excursion Diagnostics And NLR Post-Filter - 2026-07-10 22:05 KST

The next session implemented the adverse-excursion diagnostic path recommended above, then added an explicit adverse-risk post-filter experiment path to `benchmark.py`.

### New adverse-excursion diagnostic script

Added:

```text
scripts/phoenix_adverse_excursion_diagnostic.py
```

Purpose:

- Reads an existing `benchmark_trade_sim.csv`.
- Classifies active trades into `take_profit`, `stop_loss`, `time_exit`, `cash_slot`, etc.
- Writes feature diagnostics under `reports/adverse_excursion/<timestamp>/`:
  - `trade_outcomes.csv`
  - `numeric_feature_by_outcome.csv`
  - `stop_loss_vs_winners.csv`
  - `pretrade_stop_loss_vs_winners.csv`
  - `categorical_segments.csv`
  - `numeric_quantile_segments.csv`
  - `pretrade_numeric_quantile_segments.csv`
  - `summary.json`
  - `adverse_excursion_report.md`

Latest diagnostic command:

```bash
.venv/bin/python scripts/phoenix_adverse_excursion_diagnostic.py \
  --benchmark-dir reports/benchmark_20260710_174326
```

Latest output:

```text
reports/adverse_excursion/20260710_213904
```

Key result from `reports/benchmark_20260710_174326/benchmark_trade_sim.csv`:

```text
active_trades=372
stop_loss_rate=59.14%
take_profit_rate=31.72%
active_avg_return=0.1445%
active_median_return=-5.3300%
```

Important interpretation:

- No single pre-trade numeric feature cleanly separated stop-loss trades from winners.
- The strongest actionable segment signals were categorical / bucketed:
  - `sector_etf=NLR`: stop-loss rate about `77.97%`, avg trade return about `-2.20%`
  - `sector_etf=XLK`: stop-loss rate about `67.14%`, avg trade return about `-0.85%`
  - `regime=Bear Trend`: small sample, `10/10` stop-loss trades
  - `sector_etf=SOXX`, `URA`, `XLU`, `XLI` looked better than `NLR`/`XLK` in this OOS sample.
- This suggested a risk-aware post-filter should be tested before more TP/SL grid expansion.

### New offline risk-filter experiment script

Added:

```text
scripts/phoenix_risk_filter_experiment.py
```

Purpose:

- Screens adverse-risk skip/penalty ideas against existing benchmark outputs without rerunning the full model.
- Uses two layers:
  - `actual_top5_skip_to_cash`: existing Top5 trade rows, hard skipped rows become cash slots using actual trade-sim outcomes.
  - `proxy_rerank_top_candidates`: Top10 candidate rerank using a forward-return proxy; this is only a coarse screen, not a full trade simulator.

Latest command:

```bash
.venv/bin/python scripts/phoenix_risk_filter_experiment.py \
  --benchmark-dir reports/benchmark_20260710_174326
```

Latest output:

```text
reports/risk_filter_experiment/20260710_215145
```

Actual Top5 skip-to-cash results from `scenario_summary.csv`:

```text
baseline:
  portfolio_return_by_date_mean=0.14145%
  portfolio_mdd=27.98%
  stop_loss_rate=59.14%
  cash_weight=2.11%

hard_skip_nlr_only:
  portfolio_return_by_date_mean=0.48239%
  portfolio_mdd=18.53%
  stop_loss_rate=55.59%
  cash_weight=17.63%

hard_skip_xlk_only:
  portfolio_return_by_date_mean=0.29745%
  portfolio_mdd=22.91%
  stop_loss_rate=57.28%
  cash_weight=20.53%

hard_skip_bear_only:
  portfolio_return_by_date_mean=0.28171%
  portfolio_mdd=20.44%
  stop_loss_rate=58.01%
  cash_weight=4.74%

hard_skip_nlr_xlk:
  portfolio_return_by_date_mean=0.63839%
  portfolio_mdd=13.77%
  stop_loss_rate=52.26%
  cash_weight=36.05%

hard_skip_bear_nlr_xlk:
  portfolio_return_by_date_mean=0.73657%
  portfolio_mdd=11.04%
  stop_loss_rate=50.85%
  cash_weight=37.89%
```

Interpretation:

- `NLR` skip is the best first candidate because it materially improves return/MDD with a moderate cash-weight increase.
- `NLR,XLK` and `Bear+NLR+XLK` look stronger in this one sample, but cash weight jumps to about `36-38%`, so treat those as too broad until rolling OOS confirms them.
- The proxy rerank layer improved over proxy baseline but still had poor MDD; use it only as a directional screen.

### Benchmark implementation added

Changed:

```text
benchmark.py
scripts/phoenix_rolling_oos.py
scripts/phoenix_auto_cycle.sh
config/phoenix_auto_cycle.env.example
docs/operations/phoenix-auto-learning-ops.md
```

New benchmark options:

```bash
--adverse-sector-skip NLR
--adverse-regime-skip "Bear Trend"
```

Behavior:

- Default is off: empty values preserve baseline behavior.
- When enabled, matching rows are not replaced by lower-ranked tickers; they become cash slots.
- Cash slot rows are marked in `benchmark_trade_sim.csv` with:
  - `filter_reason=filtered_by_adverse_sector` or `filtered_by_adverse_regime`
  - `exit_reason=CASH`
  - `is_cash_slot=True`
  - `is_active_trade=False`
  - `adverse_filter_type`
  - `adverse_filter_value`
- `benchmark_trade_summary.csv` records counts such as `count_filtered_by_adverse_sector` and the configured skip lists.
- The same filters are passed to:
  - main trade simulation
  - trade random baseline cache
  - train grid search
  - fixed-rule OOS evaluation
  - rank-mode comparison
  - rolling OOS wrapper
  - auto-cycle wrapper

Auto-cycle env example additions:

```bash
# Experimental adverse-risk post-filter. Leave empty for baseline behavior.
# Initial candidate from diagnostics: PHOENIX_ADVERSE_SECTOR_SKIP=NLR
PHOENIX_ADVERSE_SECTOR_SKIP=
PHOENIX_ADVERSE_REGIME_SKIP=
```

Operational doc updated with the same guidance in:

```text
docs/operations/phoenix-auto-learning-ops.md
```

### Smoke validation

Short smoke command run:

```bash
.venv/bin/python benchmark.py \
  --config config/config.yaml \
  --start 2025-01-16 \
  --end 2025-01-31 \
  --top-n 5 \
  --top-list 5 \
  --period 5y \
  --frequency weekly \
  --max-dates 2 \
  --random-baseline 10 \
  --bootstrap 10 \
  --rank-mode decision \
  --xgb-blend-weight 0.0 \
  --trade-sim \
  --take-profit 0.10 \
  --stop-loss 0.05 \
  --hold-days 10 \
  --min-dollar-volume 10000000 \
  --min-price 5 \
  --max-gap-open 0.08 \
  --entry-penalty-bps 20 \
  --adverse-sector-skip NLR
```

Smoke output:

```text
reports/benchmark_20260710_220007
```

Confirmed:

- `benchmark_trade_summary.csv` has `adverse_sector_skip=NLR` and `count_filtered_by_adverse_sector=1`.
- `benchmark_trade_sim.csv` has `NNE`, `sector_etf=NLR`, `filter_reason=filtered_by_adverse_sector`, `exit_reason=CASH`.

### Verification run after implementation

Passed:

```bash
.venv/bin/python -m py_compile benchmark.py scripts/phoenix_rolling_oos.py scripts/phoenix_risk_filter_experiment.py scripts/phoenix_adverse_excursion_diagnostic.py
bash -n scripts/phoenix_auto_cycle.sh
.venv/bin/python tests/test_core_synthetic.py
```

### Current process state

As of this handoff update, the old auto-cycle parent process was still present:

```text
PID 1302639 bash /home/sysadmin/phoenix_ai_core_mvp/scripts/phoenix_auto_cycle.sh
```

The earlier child `benchmark.py` PID `1303283` was no longer present. Re-check with:

```bash
ps -p 1302639,1303283 -o pid,ppid,stat,etime,cmd
```

### Current git state at this continuation

Expected modified/untracked files include the prior handoff changes plus new diagnostic/filter work:

```text
 M benchmark.py
 M config/phoenix_auto_cycle.env.example
 M docs/operations/phoenix-auto-learning-ops.md
 M phoenix_core/engines/ranking_engine.py
 M phoenix_core/services/telegram_command_bot.py
 M scripts/phoenix_auto_cycle.sh
 M scripts/phoenix_rolling_oos.py
 M tests/test_core_synthetic.py
?? docs/handoff/2026-07-10-auto-learning-data-coverage-handoff.md
?? scripts/phoenix_adverse_excursion_diagnostic.py
?? scripts/phoenix_data_coverage_audit.py
?? scripts/phoenix_risk_filter_experiment.py
```

Notes:

- `phoenix_core/engines/ranking_engine.py`, `phoenix_core/services/telegram_command_bot.py`, and parts of `tests/test_core_synthetic.py` predate this adverse-filter continuation. Do not revert them.
- Generated reports under `reports/` are ignored and not shown in git status.

### Recommended next step

Run the full apples-to-apples weekly train/test OOS with only `NLR` adverse sector skip first. Do not enable `XLK` or `Bear Trend` in the first full run.

Recommended command:

```bash
.venv/bin/python benchmark.py \
  --config config/config.yaml \
  --train-test \
  --train-start 2023-11-01 \
  --train-end 2024-12-20 \
  --test-start 2025-01-16 \
  --test-end 2026-07-06 \
  --top-n 5 \
  --period 5y \
  --frequency weekly \
  --random-baseline 1000 \
  --bootstrap 1000 \
  --train-top-k-rules 10 \
  --tp-list 0.05,0.06,0.08,0.10 \
  --sl-list 0.02,0.03,0.04,0.05 \
  --hold-list 5,7,10 \
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

Compare against baseline:

```text
reports/benchmark_train_test_20260710_195201
```

Decision criteria before any operational env change:

- OOS `portfolio_alpha` improves, not just raw return.
- OOS `portfolio_mdd` drops materially.
- `portfolio_p_value` does not worsen materially.
- cash weight remains acceptable. `NLR` offline cash weight was about `17.6%`; much higher in full OOS should be treated cautiously.

If the full `NLR` run improves OOS, then consider a second controlled run with:

```bash
--adverse-sector-skip NLR --adverse-regime-skip "Bear Trend"
```

Only after rolling OOS confirms the improvement should host-local `config/phoenix_auto_cycle.env` be changed. Do not change deployed env based only on the offline screening report.
