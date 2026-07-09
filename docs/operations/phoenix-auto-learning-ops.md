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

## Promotion Policy

Do not weaken promotion thresholds just to get more frequent promotions. If candidates keep failing, inspect the failure reasons first.

Current conservative defaults:

- Decision-only ranking is promotable by default.
- XGB/ranking promotion is blocked unless explicitly allowed.
- Leakage audit is required.
- Rolling OOS can be required for stricter promotion.
- Telegram bot restarts only after successful promotion.

## Next Improvement Loop

1. Collect feedback for recent Telegram `/top` candidates.
2. Run `phoenix_feedback_summary.py`.
3. Compare feedback categories with rejected/promoted candidate metrics.
4. Convert repeated failure modes into a feature, filter, or gate.
5. Validate each change independently with purged train/test and rolling OOS.
