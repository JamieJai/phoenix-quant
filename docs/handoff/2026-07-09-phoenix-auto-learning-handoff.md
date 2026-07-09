# Phoenix Auto Learning Handoff - 2026-07-09

## Scope Completed

Built an automatic validation and guarded promotion pipeline for Phoenix Quant Telegram reference models/rules.

Important boundary:

- No automatic trading was added.
- The pipeline only validates and promotes Telegram reference candidate models/rules.
- Telegram bot restart happens only after a successful promotion to `models/current/`.

## Files Added

### Auto Cycle / Promotion

- `scripts/phoenix_auto_cycle.sh`
  - Hourly cycle entrypoint.
  - Uses `flock` to prevent overlapping runs.
  - Writes logs to `logs/phoenix_auto_validation.log`.
  - Builds candidates under `models/candidates/YYYYMMDD_HHMMSS/`.
  - Runs `main.py --top --top-n 5 --refresh` against the candidate model dir.
  - Runs `benchmark.py --train-test` using the actual current benchmark CLI.
  - Runs leakage audit and optional rolling OOS validation.
  - Calls promotion gate.
  - Restarts `phoenix-telegram-bot.service` only if promotion succeeds.

- `scripts/phoenix_model_gate.py`
  - Creates `metrics.json` from `benchmark_oos_rules.csv`.
  - Compares candidate versus `models/current/metrics.json`.
  - Archives previous current model to `models/archive/YYYYMMDD_HHMMSS/` before promotion.
  - Rejects candidates that fail any required gate.
  - Blocks XGB/ranking promotion by default unless `--allow-xgb-promotion` is passed.
  - Supports required leakage audit and rolling OOS checks.

- `scripts/phoenix_leakage_audit.py`
  - Checks train/test ordering, embargo, report dir separation, OOS rule presence, and test-end recency.
  - Writes `leakage_audit.json`.

- `scripts/phoenix_rolling_oos.py`
  - Runs multiple purged train/test OOS splits.
  - Writes `rolling_oos_summary.json`.
  - Intended to prevent one-window overfitting.

### Operations / Feedback

- `scripts/phoenix_auto_status.py`
  - Summarizes `models/current`, recent candidates, gate reasons, audit/rolling status, and log tail.

- `scripts/phoenix_failure_analysis.py`
  - Aggregates recent `gate.reasons`.
  - Categorizes failures into `p_value`, `rolling_oos`, `leakage_audit`, `xgb_or_rank_mode`, `mdd`, `alpha`, `active_trades`, `sample_size`, etc.
  - Prints recommended next actions based on repeated failure categories.

- `scripts/phoenix_add_feedback.py`
  - Appends structured operator feedback to `data/operator_feedback.csv`.
  - Supports percent inputs like `--return-5d 4.5%` and stores decimal returns.

- `scripts/phoenix_feedback_summary.py`
  - Summarizes operator feedback labels, reason categories, tickers, and average returns.

- `config/operator_feedback.csv.example`
  - Feedback CSV schema/example.

- `docs/operations/phoenix-auto-learning-ops.md`
  - Daily status routine, failure analysis routine, feedback capture, and promotion policy.

### Systemd / Config

- `systemd/phoenix-auto-cycle.service`
- `systemd/phoenix-auto-cycle.timer`
  - Timer runs hourly with `RandomizedDelaySec=5m`.

- `config/phoenix_auto_cycle.env.example`
  - Conservative defaults:
    - `PHOENIX_RANK_MODE=decision`
    - `PHOENIX_XGB_BLEND_WEIGHT=0.0`
    - `PHOENIX_ALLOW_XGB_PROMOTION=0`
    - `PHOENIX_REQUIRE_LEAKAGE_AUDIT=1`
    - `PHOENIX_REQUIRE_ROLLING_OOS=1`

- `.gitignore`
  - Ignores local runtime `config/phoenix_auto_cycle.env`.

## Installed On Host

The user installed and enabled the systemd timer successfully:

```text
phoenix-auto-cycle.timer loaded/enabled/active (waiting)
Next trigger: 2026-07-09 14:02:34 KST
```

At the time of this handoff, the first timer run had not completed yet, so no candidate artifacts existed under `models/candidates/`.

## Validation Performed

Syntax checks:

```bash
bash -n scripts/phoenix_auto_cycle.sh
python3 -m py_compile   scripts/phoenix_model_gate.py   scripts/phoenix_leakage_audit.py   scripts/phoenix_rolling_oos.py   scripts/phoenix_auto_status.py   scripts/phoenix_failure_analysis.py   scripts/phoenix_add_feedback.py   scripts/phoenix_feedback_summary.py
```

Behavior checks:

- Gate rejects candidate when rolling OOS is required but missing.
- Gate accepts a passing rolling summary as satisfying rolling OOS guard.
- Gate still rejects weak sample candidate on p-value / rank-mode guard as expected.
- Leakage audit passes existing valid train/test sample artifacts.
- Status script works when no current/candidate exists.
- Failure analysis works when no candidates exist and against fake candidate metrics.
- Feedback add/summary scripts work against a temporary CSV.

## Current Operating Commands

After the first auto cycle finishes:

```bash
cd /home/sysadmin/phoenix_ai_core_mvp
./scripts/phoenix_auto_status.py
./scripts/phoenix_failure_analysis.py --limit 50
```

Feedback capture example:

```bash
./scripts/phoenix_add_feedback.py   --as-of 2026-07-09   --ticker NVDA   --source telegram_top   --rank 1   --decision watch   --label good   --return-5d 4.5%   --reason trend_following   --market-context "semis strong"   --notes "clean setup"
```

Summarize feedback:

```bash
./scripts/phoenix_feedback_summary.py --feedback-csv data/operator_feedback.csv
```

## Next Recommended Tasks

1. Wait for the first timer-driven cycle to complete.
2. Run `phoenix_auto_status.py` and inspect:
   - candidate creation
   - leakage audit status
   - rolling OOS status
   - gate reasons
   - promotion/restart result
3. Run `phoenix_failure_analysis.py --limit 50` once candidates exist.
4. Pick the next engineering task from repeated failure categories, not isolated failures.
5. Start capturing manual Telegram candidate feedback with `phoenix_add_feedback.py`.
6. Do not loosen p-value, rolling OOS, or leakage gates just to force more promotions.
7. Keep XGB/ranking promotion disabled unless separate rolling OOS proves it consistently beats decision-only.
8. Keep similarity cluster dedupe as a separate experiment after rank-mode policy is stable.

## Notes

- `config/phoenix_auto_cycle.env` exists locally for the installed service but is intentionally ignored by git.
- Systemd service currently runs as root when triggered by the system timer. Generated files may be root-owned.
- If that becomes inconvenient, add a service user policy later, but confirm bot restart permissions first.
