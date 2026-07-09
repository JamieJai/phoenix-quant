# Phoenix Quant Handoff — 2026-07-08

## Version Cleanup

- User-facing CLI/report headers were still showing `Phoenix Quant v1.2`.
- Current integrated platform should be exposed as `Phoenix Quant Platform: v2.1.1`.
- `v1.2` should be treated as legacy report format compatibility, not the current platform version.

## Files Updated

- `main.py`
  - argparse description now uses `PHOENIX_QUANT_VERSION = "v2.1.1"`.
- `phoenix_core/pipeline.py`
  - analyze report header now uses `Phoenix Quant v2.1.1`.
  - ranking report header now uses `Phoenix Quant v2.1.1 Ranking`.
- `README.md`
  - version summary now separates platform version from legacy report format.

## v1.3 / v2.x Handoff Priorities

1. Similarity date-cluster dedupe
   - Avoid counting multiple tickers from the same market shock date as independent evidence.
   - Implement in `similarity_engine.py` with feature flag.

2. Daily / Intraday scenario labels
   - Add four-quadrant interpretation in `intraday_overlay_ranker.py`.
   - Display friendly Korean scenario messages in Telegram overlay.

3. Event Shock Proxy
   - Do not call it Earnings Window until an actual earnings calendar exists.
   - Start with gap + volume shock + post-gap selloff proxy.

4. Sector 5D / 20D / 60D split
   - Separate short/mid/long sector context.
   - Prefer penalty relaxation over contrarian bonus in early versions.

5. VWAP normalization
   - Normalize VWAP distance by ATR or z-score.
   - Keep raw `vwap_position_pct` for display compatibility.


## 2026-07-08 Plan Review Update

### Accepted

1. Benchmark rank mismatch is a real validation bug.
   - Production `/top` uses `RankingEngine` and sorts by `final_rank_score`.
   - Previous `benchmark.py` OOS selection sorted only by `decision.suitability_score`.
   - This means reported OOS results did not necessarily validate the exact list users saw in Telegram `/top`.
   - Fix priority: high.

2. XGB-assisted ranking must be independently measurable.
   - `decision` mode should remain as the historical baseline.
   - `ranking` mode should validate the production-like ranking formula.
   - `both` mode should produce same-date side-by-side comparisons from the same candidate pool.
   - Store `xgb_score` and `final_rank_score` in benchmark detail outputs so result audits are possible.

3. XGB blend weight should be a grid, not a hidden constant.
   - Default should stay `0.30` for compatibility with current `/top` behavior.
   - `0.0` must collapse to suitability-only selection.
   - Useful first grid: `0.0,0.1,0.2,0.3,0.4,0.5`.

4. Similarity date clustering is valid, but separate.
   - Current `similarity_engine.py` only keeps the strongest neighbor per exact date.
   - A ±3 calendar-day or trading-day cluster dedupe may reduce market-shock overcounting.
   - It must be feature-flagged and evaluated separately from rank-mode changes.

### Pushback / Corrections

1. Do not claim XGB improves OOS until `ranking` mode beats `decision` mode out-of-sample.
   - The current production rank formula is plausible, but not yet validated as superior.
   - The comparison table should be treated as evidence gathering, not proof.

2. Do not bundle rank-mode validation with similarity cluster dedupe.
   - Both affect candidate selection and confidence.
   - Combining them would make attribution impossible.
   - Sequence should be: first rank-mode parity, then similarity cluster dedupe.

3. ±3 day dedupe needs fallback behavior.
   - If cluster dedupe leaves too few neighbors, fallback should widen search or return exact-date dedupe results.
   - Required options: ON/OFF flag, cluster window size, minimum neighbor count, and clear metadata in results.

4. XGB blend weight above 0.5 should not be first-line.
   - The model is trained from historical labels and can overfit regime artifacts.
   - First sweep should stop at 0.5 unless strong OOS evidence supports more.

### Current Implementation Plan

1. Rank-mode benchmark parity.
   - Add `--rank-mode {decision,ranking,both}` to `benchmark.py`.
   - Add `--xgb-blend-weight` as a single value or comma grid.
   - In ranking mode, sort by `(1 - weight) * suitability_score + weight * xgb_score * 100`.
   - Save `benchmark_rank_mode_comparison.csv` with portfolio return, random mean, alpha, p-value, MDD, active trades, and cash slots.

2. Validation pass.
   - Run synthetic core test.
   - Run a small benchmark smoke test for `--rank-mode both --xgb-blend-weight 0.0,0.3`.
   - Confirm `benchmark_detail.csv` includes `xgb_score` and `final_rank_score`.

3. Next separate change: similarity cluster dedupe.
   - Add options to `SimilarityQuery` / similarity engine config for cluster dedupe.
   - Implement exact-date, ±N-day cluster, and OFF modes.
   - Add fallback when neighbor count drops below a minimum.
   - Re-run purged train/test OOS with one feature flag changed at a time.


## 2026-07-08 Deferred Task Snapshot

### Current Working Tree

Uncommitted local changes exist and should be reviewed before commit:

- `benchmark.py`
  - Adds `--rank-mode {decision,ranking,both}`.
  - Adds `--xgb-blend-weight` as single value or comma grid.
  - Adds ranking-mode selection using `final_rank_score`.
  - Adds `benchmark_rank_mode_comparison.csv` output.
  - Adds `xgb_score`, `final_rank_score`, `rank_mode`, `rank_score`, and `xgb_blend_weight` to benchmark candidate/detail outputs.
- `phoenix_core/models.py`
  - Adds `RankingInput.xgb_blend_weight = 0.30`.
- `phoenix_core/engines/ranking_engine.py`
  - Replaces hard-coded 0.30 blend with `RankingInput.xgb_blend_weight` while preserving default `/top` behavior.
- `docs/handoff/2026-07-08-phoenix-quant-handoff.md`
  - Adds accepted/pushback plan review and this deferred task snapshot.

### What Was Lightly Checked

Only lightweight checks were run. Do not treat them as full validation:

- Python compile check passed for changed Python files.
- `tests/test_core_synthetic.py` passed.
- One-date smoke benchmark passed for `--rank-mode both --xgb-blend-weight 0.0,0.3`.
- `main.py --top --top-n 3 --period 3y` passed.

### Do Not Claim Yet

Do not claim that XGB-assisted ranking improves OOS performance yet.
The smoke run is not enough. The current change only gives the project the tooling to measure decision-only versus XGB-assisted ranking fairly.

### Remaining Tasks

1. Review the uncommitted diff.
   - Check `benchmark.py` helper placement and naming.
   - Confirm `benchmark_detail.csv` semantics are acceptable when `--rank-mode both` chooses primary ranking output.
   - Confirm the primary weight rule: if `0.30` is present in the grid, use it for primary detail outputs; otherwise use the first supplied weight.

2. Run full rank-mode OOS comparison when resources allow.
   - Suggested command shape:
     `benchmark.py --train-test --rank-mode both --xgb-blend-weight 0.0,0.1,0.2,0.3,0.4,0.5 ...`
   - Use the same purged train/test windows as the latest accepted benchmark.
   - Keep random baseline and bootstrap settings consistent with prior v2.0.1 validation.

3. Decide whether to commit current rank-mode tooling.
   - If accepted, commit the 4 changed files together.
   - Suggested commit message: `Add benchmark rank mode comparison`.

4. After full OOS, decide production ranking policy.
   - If `ranking w=0.30` beats decision-only robustly, keep current `/top` formula.
   - If lower weights win, consider changing production default only after a separate PR/commit.
   - If decision-only wins, consider setting production blend to 0.0 or feature-flagging XGB ranking.

5. Separate follow-up: similarity cluster dedupe.
   - Do not combine with rank-mode validation.
   - Current engine only exact-date dedupes neighbors.
   - Add feature flag for OFF / exact-date / ±N-day cluster dedupe.
   - Add fallback if cluster dedupe leaves too few neighbors.
   - Re-run purged train/test with only this flag changed.

6. Telegram service note.
   - `/top` commands spawn `main.py` in a subprocess, so benchmark changes do not directly affect polling command handling.
   - Still restart `phoenix-telegram-bot.service` after code changes so the running service is aligned with the working tree.

## Validation Rule

Do not claim improvement from one combined change.
Each feature must be independently toggled and re-tested with purged train/test OOS validation.


## 2026-07-09 Rank-Mode OOS Update

Full purged train/test rank-mode comparison was run with the prior long OOS window:

- Command shape:
  `benchmark.py --train-test --train-start 2023-01-01 --train-end 2024-12-20 --test-start 2025-01-16 --test-end 2026-07-06 --period 5y --frequency monthly --top-n 10 --random-baseline 1000 --bootstrap 1000 --min-price 5 --min-dollar-volume 10000000 --max-gap-open 0.08 --entry-penalty-bps 20 --rank-mode both --xgb-blend-weight 0.0,0.1,0.2,0.3,0.4,0.5`
- Train report:
  `reports/benchmark_20260709_091409`
- Test report:
  `reports/benchmark_20260709_094256`
- Train/test wrapper:
  `reports/benchmark_train_test_20260709_101317`

### Test Rank-Mode Comparison

From `reports/benchmark_20260709_094256/benchmark_rank_mode_comparison.csv`:

| rank_mode | xgb_weight | portfolio_mean | random_mean | alpha | p_value | mdd |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| decision | 0.0 | 0.006347 | 0.004734 | 0.001614 | 0.2687 | 0.0587 |
| ranking | 0.0 | 0.006347 | 0.004734 | 0.001614 | 0.2687 | 0.0587 |
| ranking | 0.1 | 0.006112 | 0.004734 | 0.001378 | 0.3007 | 0.0529 |
| ranking | 0.2 | 0.005576 | 0.004734 | 0.000843 | 0.3616 | 0.0529 |
| ranking | 0.3 | 0.003910 | 0.004734 | -0.000823 | 0.6274 | 0.0577 |
| ranking | 0.4 | 0.003899 | 0.004734 | -0.000834 | 0.6294 | 0.0606 |
| ranking | 0.5 | 0.004805 | 0.004734 | 0.000071 | 0.4785 | 0.0628 |

### Interpretation

- `decision` and `ranking w=0.0` are identical, as expected.
- In train, higher XGB weights looked better, but the pattern did not hold in test.
- In this OOS window, production-like `ranking w=0.30` did not beat decision-only ranking.
- Do not claim XGB-assisted ranking improves OOS from this result.

### Next Recommended Action

Before changing production ranking, either:

1. Run at least one more purged OOS window / rolling split to confirm the result.
2. If the same pattern holds, consider setting production `xgb_blend_weight` to `0.0` or making XGB-assisted ranking feature-flagged.
3. Keep similarity cluster dedupe as a separate experiment after rank-mode policy is decided.
