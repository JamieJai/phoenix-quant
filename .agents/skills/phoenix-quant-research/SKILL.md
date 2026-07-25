---
name: phoenix-quant-research
description: Audit Phoenix Quant operations, cached market data, champion/challenger evidence, operator feedback, and cross-market research hypotheses. Use when Codex is asked for "일일 점검 명령 실행", a daily Phoenix review, auto-learning health check, model failure analysis, feature experiment design, OOS ablation, or a recommendation about whether a candidate or overlay is ready for promotion.
---

# Phoenix Quant Research

Run a reproducible research audit before offering market or model conclusions.

Treat the exact Korean phrase `일일 점검 명령 실행` as an explicit request to run this entire workflow with no further clarification.

## Workflow

1. Read `AGENTS.md` and `references/research-policy.md`.
2. Generate a packet:

```bash
.venv/bin/python scripts/phoenix_research_packet.py
.venv/bin/python scripts/phoenix_daily_check.py --json
```

3. Run operational evidence checks:

```bash
.venv/bin/python scripts/phoenix_auto_status.py --models-root models --json

.venv/bin/python scripts/phoenix_cross_market_report.py --ticker NVDA --ticker AMD --ticker AVGO --ticker TSM --json
.venv/bin/python scripts/phoenix_failure_analysis.py --models-root models --json
.venv/bin/python scripts/phoenix_data_coverage_audit.py --config config/config.yaml --cache-dir data --include-etfs --max-age-days 4 --min-split-coverage 0.90 --min-universe-usable-ratio 0.90 --json
```

4. Separate findings into:
   - operational health and data integrity
   - validated model evidence
   - current market context
   - hypotheses that still require testing
5. Stop promotion-oriented work when coverage, freshness, leakage, or rolling OOS evidence fails.
6. For a new feature, write its exact point-in-time formula, availability timestamp, missing-value policy, expected mechanism, and frozen ablation plan before implementation.
7. Implement only the scoped experiment requested. Do not change deployed weights, host-local env, the pause file, or the champion unless explicitly requested.
8. Verify with focused tests, syntax checks, and identical-window base-versus-overlay OOS evidence.

## Output contract

Lead with one of: `HEALTHY`, `DEGRADED`, `BLOCKED`, or `EXPERIMENTAL`.

Report:

- blocking operational or data issues
- champion and challenger evidence with caveats
- cross-market observations distinguished from validated features
- one highest-value next experiment
- files or settings changed, if any
- commands and results used for verification

Never translate an uncalibrated Codex opinion directly into a production score or trading instruction.
